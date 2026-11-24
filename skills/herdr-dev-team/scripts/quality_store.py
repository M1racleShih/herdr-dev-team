"""Plans and check execution records for the optional six-pack quality protocol."""

import json
from pathlib import Path
import re
import shlex

from pack_store import encode, guard_message, now, read_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS quality_plans (
    run_id TEXT NOT NULL REFERENCES six_runs(run_id), plan_version INTEGER NOT NULL,
    plan TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT, approval_evidence TEXT,
    base_version INTEGER, decision TEXT, created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, plan_version)
);
CREATE TABLE IF NOT EXISTS quality_assignments (
    assignment_id TEXT PRIMARY KEY REFERENCES assignments(id),
    run_id TEXT NOT NULL, plan_version INTEGER NOT NULL,
    FOREIGN KEY (run_id, plan_version) REFERENCES quality_plans(run_id, plan_version)
);
CREATE TRIGGER IF NOT EXISTS pin_quality_assignment AFTER INSERT ON assignments
BEGIN
    INSERT INTO quality_assignments (assignment_id, run_id, plan_version)
        SELECT NEW.id, run_id, plan_version FROM quality_plans
        WHERE run_id = NEW.run_id ORDER BY plan_version DESC LIMIT 1;
END;
CREATE TABLE IF NOT EXISTS quality_executions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
    run_id TEXT NOT NULL, assignment_id TEXT NOT NULL REFERENCES quality_assignments(assignment_id),
    plan_version INTEGER NOT NULL, spec_version INTEGER NOT NULL, spec_sha256 TEXT NOT NULL,
    check_id TEXT NOT NULL, check_definition TEXT NOT NULL, owner TEXT NOT NULL, role TEXT NOT NULL,
    use TEXT NOT NULL, "commit" TEXT NOT NULL, key TEXT NOT NULL, source TEXT, directory TEXT NOT NULL,
    lock_device INTEGER NOT NULL, lock_inode INTEGER NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT NOT NULL, summary TEXT,
    return_code INTEGER, tool TEXT, configuration TEXT, evidence TEXT,
    UNIQUE (assignment_id, key),
    FOREIGN KEY (run_id, plan_version) REFERENCES quality_plans(run_id, plan_version),
    FOREIGN KEY (run_id, spec_version) REFERENCES four_specs(run_id, version)
);
CREATE INDEX IF NOT EXISTS latest_quality_execution
    ON quality_executions(run_id, plan_version, spec_version, spec_sha256, check_id, "commit", sequence);
CREATE TABLE IF NOT EXISTS quality_final (
    run_id TEXT PRIMARY KEY REFERENCES six_runs(run_id), receipt_id INTEGER NOT NULL REFERENCES receipts(id),
    executions TEXT NOT NULL
);
"""

OWNERS = ("coder", "cleaner", "architect", "hardener", "qa")
PLAN_FIELDS = {"schema", "approved_examples_check", "hardening_check", "qa_command", "checks"}
CHECK_FIELDS = {"id", "owner", "use", "scope", "criterion", "argv", "protocol"}
OPTIONAL_FIELDS = {"timeout_seconds", "fail_codes"}
CHECK_ID = re.compile(r"[A-Za-z0-9._-]+\Z")


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def validate_check(check):
    if not isinstance(check, dict) or not CHECK_FIELDS <= check.keys() <= CHECK_FIELDS | OPTIONAL_FIELDS:
        raise ValueError("quality check requires id, owner, use, scope, criterion, argv and protocol")
    name = check["id"]
    if not isinstance(name, str) or not CHECK_ID.fullmatch(name) or name == "user-entry":
        raise ValueError("quality check id must use letters, digits, '.', '_' or '-' and cannot be user-entry")
    if check["owner"] not in OWNERS:
        raise ValueError(f"invalid owner for check {name}")
    if check["use"] not in ("stage", "final", "observe"):
        raise ValueError(f"invalid use for check {name}")
    if check["protocol"] not in ("exit", "result"):
        raise ValueError(f"invalid protocol for check {name}")
    scope, argv = check["scope"], check["argv"]
    if not isinstance(scope, list) or not scope or not all(valid_text(item) for item in scope):
        raise ValueError(f"check {name} requires a nonempty scope list of project components or inputs")
    if not valid_text(check["criterion"]):
        raise ValueError(f"check {name} requires a nonempty criterion")
    if (not isinstance(argv, list) or not argv or not valid_text(argv[0])
            or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)):
        raise ValueError(f"check {name} requires argv with a nonempty executable and string arguments")
    if "timeout_seconds" in check:
        timeout = check["timeout_seconds"]
        if type(timeout) is not int or timeout <= 0:
            raise ValueError(f"check {name} timeout_seconds must be a positive integer")
    if check["protocol"] == "exit":
        codes = check.get("fail_codes")
        if not isinstance(codes, list) or not all(type(code) is int and 1 <= code <= 255 for code in codes):
            raise ValueError(f"check {name} fail_codes must list integers from 1 to 255 for the exit protocol")
    elif "fail_codes" in check:
        raise ValueError(f"check {name} fail_codes applies only to the exit protocol")
    for value in (check["criterion"], *scope, *argv):
        guard_message(value)
    return check


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.keys() != PLAN_FIELDS or type(plan["schema"]) is not int or plan["schema"] != 1:
        raise ValueError("quality plan requires schema 1, approved_examples_check, hardening_check, qa_command and checks")
    if not valid_text(plan["qa_command"]):
        raise ValueError("quality plan requires a nonempty qa_command")
    guard_message(plan["qa_command"])
    qa_argv = shlex.split(plan["qa_command"])
    if not qa_argv or not valid_text(qa_argv[0]):
        raise ValueError("qa_command must name a QA interpreter or executable")
    if not isinstance(plan["checks"], list):
        raise ValueError("quality plan checks must be a list")
    checks = {}
    for check in plan["checks"]:
        name = validate_check(check)["id"]
        if name in checks:
            raise ValueError(f"duplicate quality check {name}")
        checks[name] = check
    for field, owner in (("approved_examples_check", "coder"), ("hardening_check", "hardener")):
        reference = plan[field]
        choice = checks.get(reference) if isinstance(reference, str) else None
        if not choice or choice["owner"] != owner or choice["use"] == "observe":
            raise ValueError(f"{field} must name a {owner} stage or final check")
    return plan


def read_plan(path):
    return validate_plan(json.loads(read_text(Path(path))))


def read_check(path):
    return validate_check(json.loads(read_text(Path(path))))


def has_plans(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'quality_plans'").fetchone() is not None


def plan_view(row):
    view = dict(row)
    view["plan"] = json.loads(row["plan"])
    view["decision"] = json.loads(row["decision"]) if row["decision"] else None
    return view


def get_plan(db, run_id, version=None):
    if not has_plans(db):
        return None
    row = db.execute(
        "SELECT * FROM quality_plans WHERE run_id = ? AND plan_version = COALESCE(?, plan_version) "
        "ORDER BY plan_version DESC LIMIT 1", (run_id, version),
    ).fetchone()
    return plan_view(row) if row else None


def plan_history(db, run_id):
    return [plan_view(row) for row in db.execute(
        "SELECT * FROM quality_plans WHERE run_id = ? ORDER BY plan_version", (run_id,),
    )]


def record_plan(db, run_id, version, plan, *, kind, reason, approval_evidence, base_version, decision):
    db.execute(
        "INSERT INTO quality_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, version, encode(plan), kind, reason, approval_evidence, base_version,
         encode(decision) if decision is not None else None, now()),
    )


def insert_initial_plan(db, run_id, plan):
    record_plan(db, run_id, 1, plan, kind="initial", reason=None,
                approval_evidence=None, base_version=None, decision=None)


def assignment_plan(db, item):
    if not has_plans(db):
        return None
    row = db.execute(
        "SELECT p.* FROM quality_assignments a JOIN quality_plans p USING (run_id, plan_version) "
        "WHERE a.assignment_id = ?", (item["id"],),
    ).fetchone()
    return plan_view(row) if row else None


def assert_no_active(db, run_id):
    if has_plans(db):
        active = db.execute(
            "SELECT id FROM quality_executions WHERE run_id = ? AND outcome = 'running' ORDER BY sequence LIMIT 1",
            (run_id,),
        ).fetchone()
        if active:
            raise ValueError(f"check execution {active['id']} has not finished and archived")


def register_execution(db, run, item, plan, spec, check, commit, key, source, directory, lock_info):
    cursor = db.execute(
        """INSERT INTO quality_executions
           (run_id, assignment_id, plan_version, spec_version, spec_sha256, check_id,
            check_definition, owner, role, use, "commit", key, source, directory, lock_device, lock_inode,
            started_at, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
        (run["id"], item["id"], plan["plan_version"], spec["version"], spec["sha256"], check["id"],
         encode(check), check["owner"], item["role"], check["use"], commit, key, source, directory,
         lock_info.st_dev, lock_info.st_ino, now()),
    )
    execution_id = f"exec-{cursor.lastrowid}"
    db.execute("UPDATE quality_executions SET id = ? WHERE sequence = ?", (execution_id, cursor.lastrowid))
    return execution_id


def get_execution(db, execution_id):
    if not has_plans(db):
        return None
    return db.execute("SELECT * FROM quality_executions WHERE id = ?", (execution_id,)).fetchone()


def execution_for_key(db, item, key):
    return db.execute(
        "SELECT * FROM quality_executions WHERE assignment_id = ? AND key = ?", (item["id"], key),
    ).fetchone()


def temporary_definition(db, item, check_id):
    # The first call reserves the observation ID and freezes its definition for this assignment.
    return db.execute(
        "SELECT check_definition FROM quality_executions WHERE assignment_id = ? AND check_id = ? AND source IS NOT NULL "
        "ORDER BY sequence LIMIT 1", (item["id"], check_id),
    ).fetchone()


def latest_execution(db, run_id, plan_version, spec_version, spec_sha256, check_id, commit):
    return db.execute(
        'SELECT * FROM quality_executions WHERE run_id = ? AND plan_version = ? AND spec_version = ? '
        'AND spec_sha256 = ? AND check_id = ? AND "commit" = ? ORDER BY sequence DESC LIMIT 1',
        (run_id, plan_version, spec_version, spec_sha256, check_id, commit),
    ).fetchone()


def executions(db, run_id, *, running=False):
    if running:
        return db.execute(
            "SELECT * FROM quality_executions WHERE run_id = ? AND outcome = 'running' ORDER BY sequence", (run_id,),
        )
    return db.execute("SELECT * FROM quality_executions WHERE run_id = ? ORDER BY sequence", (run_id,))


def finish_execution(db, execution_id, *, outcome, summary, return_code, tool, configuration, evidence):
    db.execute(
        "UPDATE quality_executions SET finished_at = ?, outcome = ?, summary = ?, return_code = ?, "
        "tool = ?, configuration = ?, evidence = ? WHERE id = ? AND outcome = 'running'",
        (now(), outcome, summary, return_code, tool, configuration, encode(evidence), execution_id),
    )


def execution_view(row):
    view = dict(row)
    del view["lock_device"], view["lock_inode"]
    view["check_definition"] = json.loads(row["check_definition"])
    view["argv"] = view["check_definition"]["argv"]
    view["evidence"] = json.loads(row["evidence"]) if row["evidence"] else None
    view["status"] = "running" if row["outcome"] == "running" else "finished"
    return view


def record_final(db, run_id, receipt_id, execution_ids):
    db.execute("INSERT INTO quality_final VALUES (?, ?, ?)", (run_id, receipt_id, encode(execution_ids)))


def final_evidence(db, run_id):
    row = db.execute("SELECT receipt_id, executions FROM quality_final WHERE run_id = ?", (run_id,)).fetchone()
    return {"receipt_id": row["receipt_id"], "executions": json.loads(row["executions"])} if row else None
