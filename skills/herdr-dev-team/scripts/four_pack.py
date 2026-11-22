"""Four-pack approval gate, immutable example versions and role transitions."""

import json
from pathlib import Path
import re

from pack_evidence import FOUR_STATUSES, git
from pack_store import (
    accepted_receipt, assignment_id, check_dispatch_project, current_assignment, encode, get_attempt,
    insert_assignment, nonempty, now, project_baseline,
)
from spec_artifact import checked_out_spec


ROLES = {role: tuple(sorted(statuses)) for role, statuses in FOUR_STATUSES.items()}
NEXT = {"coder": "refactorer", "refactorer": "architect"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS four_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), examples TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS four_specs (
    run_id TEXT NOT NULL REFERENCES four_runs(run_id), version INTEGER NOT NULL,
    spec_commit TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL,
    source_receipt INTEGER NOT NULL REFERENCES receipts(id), approval TEXT,
    PRIMARY KEY (run_id, version)
);
"""


def examples_path(db, run):
    return db.execute("SELECT examples FROM four_runs WHERE run_id = ?", (run["id"],)).fetchone()[0]


def spec_view(row, path):
    return {"version": row["version"], "commit": row["spec_commit"], "sha256": row["sha256"],
            "body": row["body"], "path": path, "source_receipt": row["source_receipt"],
            "approval": json.loads(row["approval"]) if row["approval"] else None}


def latest_spec(db, run, at_round=None):
    rows = db.execute("SELECT * FROM four_specs WHERE run_id = ? AND version <= COALESCE(?, version) "
                      "ORDER BY version DESC LIMIT 1", (run["id"], at_round))
    row = rows.fetchone()
    return spec_view(row, examples_path(db, run)) if row else None


def spec_id(spec):
    return {"version": spec["version"], "sha256": spec["sha256"]}


def next_round(db, run):
    return db.execute("SELECT COALESCE(MAX(round), 0) + 1 FROM assignments WHERE run_id = ?",
                      (run["id"],)).fetchone()[0]


def reopen(db, run, role, commit, source=None):
    return insert_assignment(db, run, role, next_round(db, run), commit, source)


def head_at_or_after(run, anchor):
    project = Path(run["project"])
    project_id, branch, head = project_baseline(project)
    if (project_id, branch) != (run["project_id"], run["branch"]):
        raise ValueError("the worktree must be at the recorded project and branch")
    if head != anchor and git(project, "merge-base", "--is-ancestor", anchor, head,
                               allowed=(0, 1)).returncode:
        raise ValueError("the worktree HEAD must descend from the recorded commit; roll back with a new commit")
    return head


def changed_examples(db, run):
    if run["phase"] in ("ready", "completed") or (run["current"] or "").startswith("specifier:"):
        return None
    spec = latest_spec(db, run)
    current = current_assignment(db, run)
    anchor = current["input_commit"] if current else spec["commit"]
    if run["phase"] in ("blocked", "paused"):
        receipt = accepted_receipt(db, current["id"])
        anchor = json.loads(receipt["payload"])["commit"]
    head = head_at_or_after(run, anchor)
    if head != spec["commit"] and git(
            Path(run["project"]), "rev-list", "--full-history", "-1", f"{spec['commit']}..{head}",
            "--", f":(literal){spec['path']}").stdout:
        return head
    return None


YIELD_ACTION = ("Examples changed. If the worker received the assignment, have them report superseded. "
                "Reconcile any uncertain delivery before retrying.")


def retire_changed_examples(db, run, head):
    """Retire only work that was unclaimed, yielded, or confirmed not delivered."""
    previous = current_assignment(db, run)
    receipt = accepted_receipt(db, previous["id"]) if previous else None
    attempt = get_attempt(db, previous["id"]) if previous else None
    if not receipt and attempt and attempt["status"] != "not-delivered-by-inspection":
        return None
    check_dispatch_project(run, head)
    spec = latest_spec(db, run)
    following = reopen(db, run, "specifier", head, receipt["id"] if receipt else spec["source_receipt"])
    if previous is not None and not receipt:
        decision = {"phase": "specification-changed", "decided_at": now(), "next_assignment": following}
        db.execute("UPDATE assignments SET decision = ? WHERE id = ?", (encode(decision), previous["id"]))
    return following


def approve(db, run, version, sha256, evidence):
    """Called only by the recorded Lead, with the actual human's evidence."""
    evidence = nonempty(evidence, "human approval evidence")
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("use the full lowercase SHA-256 shown in resume")
    spec = latest_spec(db, run)
    if (not spec or spec["version"] != version or spec["sha256"] != sha256
            or (run["current"] or "").startswith("specifier:")):
        raise ValueError("approval must name the current submitted specification version and SHA-256")
    changed = changed_examples(db, run)
    if changed:
        replacement = retire_changed_examples(db, run, changed)
        reason = "Examples changed; Specifier must submit a new version." if replacement else YIELD_ACTION
        return {"approved": False, "rejection": reason}, replacement
    if spec["approval"]:
        return {"approved": True, "duplicate": True, **spec_id(spec)}, assignment_id(run, "coder", version)
    if run["phase"] != "waiting-for-human":
        raise ValueError("run is not waiting for human approval")
    check_dispatch_project(run, spec["commit"])
    checked_out_spec(Path(run["project"]), spec["commit"], spec["path"])
    approval = {"evidence": evidence, "leader": run["leader"], "approved_at": now()}
    db.execute("UPDATE four_specs SET approval = ? WHERE run_id = ? AND version = ?",
               (encode(approval), run["id"], version))
    return {"approved": True, **spec_id(spec)}, insert_assignment(
        db, run, "coder", version, spec["commit"], spec["source_receipt"])


def rework(db, run, *, specification=False):
    phase = run["phase"]
    if phase not in ("waiting-for-human", "paused", "blocked"):
        raise ValueError("rework requires a waiting, paused or blocked four-pack run")
    current = current_assignment(db, run)
    if current is None:
        spec = latest_spec(db, run)
        role, anchor, source = "specifier", spec["commit"], spec["source_receipt"]
    else:
        receipt = accepted_receipt(db, current["id"])
        role = "specifier" if specification else current["role"]
        anchor, source = json.loads(receipt["payload"])["commit"], receipt["id"]
    head = head_at_or_after(run, anchor)
    check_dispatch_project(run, head)
    return reopen(db, run, role, head, source)


def validate_receipt(db, store, run, item, status, payload, checks, validate):
    project = Path(run["project"])
    role = item["role"]
    path = examples_path(db, run)
    if role == "specifier":
        head_at_or_after(run, item["input_commit"])
    else:
        spec = latest_spec(db, run, item["round"])
        version = payload.get("specification") if isinstance(payload, dict) else None
        if (not spec or not spec["approval"] or not isinstance(version, dict)
                or type(version.get("version")) is not int or version != spec_id(spec)):
            raise ValueError("receipt must name the assignment's approved specification version and SHA-256")
    validate(project, run["branch"], item["input_commit"], role, status, payload, store, checks,
             pack="four", spec_path=path)
    if role == "specifier" and status == "done":
        if payload.get("examples") != path:
            raise ValueError(f"Specifier receipt examples must name the pinned project-relative path: {path}")
        return checked_out_spec(project, payload["commit"], path)
    if role != "specifier" and status not in ("paused", "blocked", "superseded"):
        artifact = checked_out_spec(project, payload["commit"], path)
        if artifact["sha256"] != spec["sha256"]:
            raise ValueError("receipt must use the exact approved examples")
    return None


def transition(db, run, item, status, receipt_id, artifact):
    role, number = item["role"], item["round"]
    phase, following = status, None
    if role == "specifier" and status == "done":
        db.execute("INSERT INTO four_specs VALUES (?, ?, ?, ?, ?, ?, NULL)",
                   (run["id"], number, artifact["commit"], artifact["sha256"], artifact["body"], receipt_id))
        phase = "waiting-for-human"
    elif status == "done":
        phase, following = "active", (NEXT[role], number)
    elif status == "fail":
        phase, following = "active", ("coder", next_round(db, run))
    elif status == "superseded":
        phase, following = "active", ("specifier", next_round(db, run))
    elif role == "architect" and status == "pass":
        phase = "completed"
    return phase, following


def resume(db, run, view):
    path = examples_path(db, run)
    versions = [spec_view(row, path) for row in db.execute(
        "SELECT * FROM four_specs WHERE run_id = ? ORDER BY version", (run["id"],))]
    phase = run["phase"]
    specifying = (run["current"] or "").startswith("specifier:")
    spec = versions[-1] if versions and not specifying else None
    view.update(examples=path, specification=spec, specification_versions=versions)
    for item in view["assignments"].values():
        if item["role"] != "specifier":
            assigned = next(version for version in reversed(versions) if version["version"] <= item["round"])
            item["specification"] = spec_id(assigned)
    if phase == "ready":
        return "Ready to assign Specifier."
    if phase == "waiting-for-human":
        return f"Waiting for human approval of examples version {spec['version']} (SHA-256 {spec['sha256']})."
    if phase == "completed":
        item = view["assignments"][next(reversed(view["assignments"]))]
        receipt, decision = item.get("receipt", {}), item.get("decision", {})
        if (item["role"] == "architect" and receipt.get("status") == "pass"
                and decision.get("phase") == "completed" and decision.get("receipt") == receipt["id"]):
            commit = view["final_commit"] = receipt["result"]["commit"]
            return f"Architect accepted {commit[:7]}; four-pack completed."
        return "Completion record needs inspection."
    item = view["assignments"][run["current"]]
    role = item["role"].capitalize()
    if phase in ("paused", "blocked"):
        return f"{role} is {phase}."
    delivery = item["delivery"]["status"]
    return f"Round {item['round']}: {role} assignment {delivery}; no accepted receipt yet."


def assignment_context(db, run, item):
    if item["role"] == "specifier":
        path = examples_path(db, run)
        return f"\nPinned examples file: {path}\n"
    spec = latest_spec(db, run, item["round"])
    if not spec or not spec["approval"]:
        raise ValueError("downstream assignment requires an approved specification")
    actual = checked_out_spec(Path(run["project"]), item["input_commit"], spec["path"])
    if actual["sha256"] != spec["sha256"]:
        raise ValueError("assignment must use the exact approved examples")
    return (f"\nApproved examples {spec['path']} at {spec['commit']}; "
            f"version {spec['version']}, SHA-256 {spec['sha256']}.\n"
            "Read the durable specification body with four RUN_ID resume.\n")


def attach_parser(commands, positive):
    command = commands.add_parser("approve", help="relay the human's approval of the exact examples")
    command.add_argument("version", type=positive)
    command.add_argument("sha256")
    command.add_argument("--evidence", required=True)
    retry = commands.add_parser("rework", help="reassign a waiting, paused or blocked role")
    retry.add_argument("--specification", action="store_true", help="return to Specifier for new examples")
