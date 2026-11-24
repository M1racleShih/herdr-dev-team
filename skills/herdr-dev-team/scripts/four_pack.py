"""Shared approval gate, immutable specification versions and serial role transitions."""

import json
from pathlib import Path
import re

from pack_evidence import FOUR_STATUSES, git, validate_checks
from pack_store import (
    accepted_receipt, assignment_id, check_dispatch_project, current_assignment, encode, get_attempt,
    insert_assignment, nonempty, now, project_baseline,
)
from quality_receipt import quality_finding_context, validate_quality
from quality_store import assignment_plan, record_final
from six_evidence import qa_mismatch, qa_repair_checks, validate_six_spec
from spec_artifact import checked_out_spec


ROLES = {role: tuple(sorted(statuses)) for role, statuses in FOUR_STATUSES.items()}
NEXT = {"coder": "refactorer", "refactorer": "architect"}
SIX_NEXT = {"coder": "cleaner", "cleaner": "architect", "architect": "hardener", "hardener": "qa"}
FINAL_ROLE = {"four": "architect", "six": "qa"}
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
SIX_SCHEMA = """
CREATE TABLE IF NOT EXISTS six_runs (
    run_id TEXT PRIMARY KEY REFERENCES four_runs(run_id)
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
        raise ValueError(f"rework requires a waiting, paused or blocked {run['pack']}-pack run")
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
    if run["pack"] == "six" and role == "qa":
        spec = latest_spec(db, run, current["round"])
        if qa_repair_finding(db, run, spec, current["round"]) is None and head != hardener_commit(
                db, run, spec, current["round"]):
            role = "coder"
    return reopen(db, run, role, head, source)


def prior_qa_finding(db, run, spec, round_number):
    # A pause leaves the prior finding active. A new approved version starts fresh.
    return db.execute(
        "SELECT r.id, r.status, r.payload, a.round FROM receipts r JOIN assignments a ON a.id = r.assignment_id "
        "WHERE a.run_id = ? AND a.role = 'qa' AND a.round >= ? AND a.round < ? "
        "AND r.accepted = 1 AND r.status IN ('fail', 'repair') ORDER BY a.round DESC LIMIT 1",
        (run["id"], spec["version"], round_number),
    ).fetchone()


def hardener_commit(db, run, spec, round_number, *, after_round=None):
    row = db.execute(
        "SELECT r.payload FROM receipts r JOIN assignments a ON a.id = r.assignment_id "
        "WHERE a.run_id = ? AND a.role = 'hardener' AND a.round BETWEEN ? AND ? "
        "AND r.accepted = 1 AND r.status = 'done' ORDER BY a.round DESC LIMIT 1",
        (run["id"], spec["version"] if after_round is None else after_round + 1, round_number),
    ).fetchone()
    return json.loads(row["payload"])["commit"] if row else None


def qa_repair_finding(db, run, spec, round_number, *, previous=None):
    if previous is None:
        previous = prior_qa_finding(db, run, spec, round_number)
    if (previous is not None and previous["status"] == "repair"
            and hardener_commit(db, run, spec, round_number, after_round=previous["round"]) is None):
        return previous
    return None


def validate_receipt(db, store, run, item, status, payload, checks, validate):
    project = Path(run["project"])
    role, pack = item["role"], run["pack"]
    path = examples_path(db, run)
    spec = None
    if role == "specifier":
        head_at_or_after(run, item["input_commit"])
    else:
        spec = latest_spec(db, run, item["round"])
        version = payload.get("specification") if isinstance(payload, dict) else None
        if (not spec or not spec["approval"] or not isinstance(version, dict)
                or type(version.get("version")) is not int or version != spec_id(spec)):
            raise ValueError("receipt must name the assignment's approved specification version and SHA-256")
    plan = assignment_plan(db, item) if pack == "six" else None
    evidence_options, final = {}, None
    if plan is not None:
        failure, final = validate_quality(db, run, item, plan, spec, status, payload)
        evidence_options.update(qa_command=plan["plan"]["qa_command"], quality_failure=failure)
    if pack == "six" and ((role == "coder" and status == "done") or role == "qa"):
        previous = prior_qa_finding(db, run, spec, item["round"])
        if previous is not None:
            prior_qa = json.loads(previous["payload"])
            if role == "qa":
                evidence_options["qa_script_snapshot"] = prior_qa["qa"]["script_snapshot"]
                if qa_repair_finding(db, run, spec, item["round"], previous=previous) is not None:
                    evidence_options["qa_repair_commit"] = prior_qa["commit"]
            elif (previous["status"] == "fail" and isinstance(payload, dict)
                  and payload.get("commit") == prior_qa["commit"]):
                # A changed plan requires new project evidence. User-entry observations still
                # refer to the same approved behavior and require an implementation change.
                stale_project_failure = (
                    plan is not None and prior_qa["failure"]["check"] != "user-entry"
                    and prior_qa["quality"]["plan_version"] != plan["plan_version"]
                )
                if not stale_project_failure:
                    raise ValueError("Coder must deliver a new commit to address the accepted QA failure")
        # Stores from older helpers may already have an ordinary QA assignment at a changed HEAD.
        if (role == "qa" and status in ("pass", "repair") and "qa_repair_commit" not in evidence_options
                and item["input_commit"] != hardener_commit(db, run, spec, item["round"])):
            raise ValueError("The assigned QA commit needs Hardener verification. Report paused for Coder rework.")
    validate(project, run["branch"], item["input_commit"], role, status, payload, store, checks,
             pack=pack, spec_path=path, **evidence_options)
    if role == "specifier" and status == "done":
        if payload.get("examples") != path:
            raise ValueError(f"Specifier receipt examples must name the pinned project-relative path: {path}")
        artifact = checked_out_spec(project, payload["commit"], path)
        if pack == "six":
            validate_six_spec(artifact["body"])
        return artifact
    if role != "specifier" and status not in ("paused", "blocked", "superseded"):
        artifact = checked_out_spec(project, payload["commit"], path)
        if artifact["sha256"] != spec["sha256"]:
            raise ValueError("receipt must use the exact approved examples")
    return final


def transition(db, run, item, status, receipt_id, artifact):
    role, number = item["role"], item["round"]
    phase, following = status, None
    if role == "specifier" and status == "done":
        db.execute("INSERT INTO four_specs VALUES (?, ?, ?, ?, ?, ?, NULL)",
                   (run["id"], number, artifact["commit"], artifact["sha256"], artifact["body"], receipt_id))
        phase = "waiting-for-human"
    elif status == "done":
        successors = SIX_NEXT if run["pack"] == "six" else NEXT
        phase, following = "active", (successors[role], number)
    elif status == "fail":
        phase, following = "active", ("coder", next_round(db, run))
    elif status == "repair":
        phase, following = "active", ("qa", next_round(db, run))
    elif status == "superseded":
        phase, following = "active", ("specifier", next_round(db, run))
    elif role == FINAL_ROLE[run["pack"]] and status == "pass":
        if run["pack"] == "six" and artifact is not None:
            record_final(db, run["id"], receipt_id, artifact)
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
        description = "examples and QA steps" if run["pack"] == "six" else "examples"
        return f"Waiting for human approval of {description} version {spec['version']} (SHA-256 {spec['sha256']})."
    if phase == "completed":
        item = view["assignments"][next(reversed(view["assignments"]))]
        receipt, decision = item.get("receipt", {}), item.get("decision", {})
        if (item["role"] == FINAL_ROLE[run["pack"]] and receipt.get("status") == "pass"
                and decision.get("phase") == "completed" and decision.get("receipt") == receipt["id"]):
            commit = view["final_commit"] = receipt["result"]["commit"]
            role = "QA" if run["pack"] == "six" else "Architect"
            return f"{role} accepted {commit[:7]}; {run['pack']}-pack completed."
        return "Completion record needs inspection."
    item = view["assignments"][run["current"]]
    role = "QA" if item["role"] == "qa" else item["role"].capitalize()
    if phase in ("paused", "blocked"):
        return f"{role} is {phase}."
    delivery = item["delivery"]["status"]
    return f"Round {item['round']}: {role} assignment {delivery}; no accepted receipt yet."


def qa_finding_context(db, spec, payload):
    expected = validate_six_spec(spec["body"])["qa_steps"]
    mismatch = qa_mismatch(expected, payload["qa"]["observations"])
    checks = {check["name"]: check["log_snapshot"] for check in payload["checks"]}
    log = checks["user-entry"]
    context = f"QA evidence at {payload['commit']}. Harness {payload['qa']['script_snapshot']}, log {log}.\n"
    if mismatch:
        context += f"Observed QA mismatch\n{mismatch}\n"
    failed_check = payload["failure"]["check"]
    if failed_check != "user-entry":
        if "quality" in payload and failed_check not in checks:
            context += quality_finding_context(db, payload)
        else:
            context += f"Failed check {json.dumps(failed_check, ensure_ascii=True)}. Log {checks[failed_check]}.\n"
    return context


def assignment_context(db, run, item, checks):
    pack, role = run["pack"], item["role"]
    if role == "specifier":
        path = examples_path(db, run)
        description = "specification JSON file for examples and QA steps" if pack == "six" else "examples file"
        return f"\nPinned {description}: {path}\n"
    spec = latest_spec(db, run, item["round"])
    if not spec or not spec["approval"]:
        raise ValueError("downstream assignment requires an approved specification")
    actual = checked_out_spec(Path(run["project"]), item["input_commit"], spec["path"])
    if actual["sha256"] != spec["sha256"]:
        raise ValueError("assignment must use the exact approved examples")
    description = "examples and QA steps" if pack == "six" else "examples"
    context = (f"\nApproved {description} {spec['path']} at {spec['commit']}; "
               f"version {spec['version']}, SHA-256 {spec['sha256']}.\n"
               f"Read the durable specification body with {pack} RUN_ID resume.\n")
    if pack != "six" or role not in ("coder", "qa"):
        return context
    plan = assignment_plan(db, item)
    previous = prior_qa_finding(db, run, spec, item["round"])
    failure = json.loads(previous["payload"]) if previous is not None else None
    if role == "qa":
        if failure is not None:
            snapshot = failure["qa"]["script_snapshot"]
            context += f"Reuse the script bytes from QA snapshot {snapshot} in the private store.\n"
        if qa_repair_finding(db, run, spec, item["round"], previous=previous) is None:
            return context + (
                "Use the assigned commit for independent QA. Report repair for a scoped implementation defect "
                "that QA can fix, fail to hand the defect to Coder, or paused for conflicting approved expectations.\n"
            )
        if item["source_receipt"] != previous["id"]:
            context += qa_finding_context(db, spec, failure)
            context += f"QA repair diagnosis {json.dumps(failure['issues'], ensure_ascii=True)}\n"
        if plan is not None:
            return context + (
                f"QA is assigned to repair the defect recorded at {failure['commit']}. Commit a scoped fix "
                "under the same approval. Rerun the original QA script, QA stage checks and all final checks "
                "without evidence for the repaired commit. Report fail with evidence for Coder rework or paused "
                "for a conflict requiring human review.\n"
            )
        repair_checks = qa_repair_checks(validate_checks(checks, pack=pack))
        return context + (
            f"QA is assigned to repair the defect recorded at {failure['commit']}. Commit a scoped fix "
            "under the same approval and rerun the frozen QA and local repair checks on clean repaired HEAD.\n"
            f"Local QA repair checks:\n{encode(list(repair_checks.values()))}\n"
            "Report pass with the changed commit, fail with evidence if the defect needs Coder, "
            "or paused if the approved behavior needs human review.\n"
        )
    if previous is not None:
        if previous["status"] == "fail":
            earlier = prior_qa_finding(db, run, spec, previous["round"])
            if earlier is not None and earlier["status"] == "repair":
                context += qa_finding_context(db, spec, json.loads(earlier["payload"]))
        context += qa_finding_context(db, spec, failure)
        if item["source_receipt"] != previous["id"]:
            finding = json.dumps(failure["failure"], ensure_ascii=True, indent=2)
            context += f"Previous QA finding at {failure['commit']}\n{finding}\n"
    if plan is not None and item["source_receipt"] is not None:
        source = db.execute(
            "SELECT r.payload FROM receipts r JOIN assignments a ON a.id = r.assignment_id "
            "WHERE r.id = ? AND r.accepted = 1 AND r.status = 'fail' AND a.role != 'qa'",
            (item["source_receipt"],),
        ).fetchone()
        if source:
            context += quality_finding_context(db, json.loads(source["payload"]))
    return context


def attach_parser(commands, positive):
    command = commands.add_parser("approve", help="relay the human's approval of the exact specification")
    command.add_argument("version", type=positive)
    command.add_argument("sha256")
    command.add_argument("--evidence", required=True)
    retry = commands.add_parser("rework", help="reassign a waiting, paused or blocked role")
    retry.add_argument("--specification", action="store_true", help="return to Specifier for a new specification")
