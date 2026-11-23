"""SQLite and Git records shared by both serial engineering packs."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from pack_evidence import git


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL, project_id TEXT NOT NULL, branch TEXT NOT NULL, baseline TEXT NOT NULL,
    pack_version INTEGER NOT NULL, leader TEXT NOT NULL, workers TEXT NOT NULL,
    rounds INTEGER NOT NULL, phase TEXT NOT NULL, current TEXT,
    card_version INTEGER NOT NULL, role_cards TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
    body TEXT NOT NULL, checks TEXT NOT NULL, created_at TEXT NOT NULL,
    approval TEXT, approved_at TEXT,
    PRIMARY KEY (run_id, version)
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    status TEXT NOT NULL, worker TEXT NOT NULL, received_at TEXT NOT NULL,
    submitted TEXT NOT NULL, payload TEXT NOT NULL, accepted INTEGER NOT NULL, reason TEXT,
    notification TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_receipt ON receipts(assignment_id) WHERE accepted = 1;
CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL, round INTEGER NOT NULL,
    target TEXT NOT NULL, input_commit TEXT NOT NULL, card_version INTEGER NOT NULL,
    created_at TEXT NOT NULL, source_receipt INTEGER REFERENCES receipts(id), decision TEXT,
    UNIQUE (run_id, role, round), FOREIGN KEY (run_id, card_version) REFERENCES cards(run_id, version)
);
CREATE TABLE IF NOT EXISTS attempts (
    assignment_id TEXT NOT NULL REFERENCES assignments(id), number INTEGER NOT NULL,
    status TEXT NOT NULL, intent_at TEXT NOT NULL, finished_at TEXT, outcome TEXT,
    inspection TEXT, inspected_at TEXT,
    PRIMARY KEY (assignment_id, number)
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def nonempty(value, label):
    if not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def guard_message(value):
    if any(not ch.isprintable() and ch not in "\n\t" for ch in value):
        raise ValueError("dispatch text or path contains terminal control characters")
    return value


def read_text(path):
    return nonempty(path.read_text(encoding="utf-8"), str(path))


def project_baseline(project):
    root = Path(os.fsdecode(git(project, "rev-parse", "--show-toplevel").stdout).rstrip("\n")).resolve()
    if project != root:
        raise ValueError("--project must name the worktree root")
    common_output = os.fsdecode(git(project, "rev-parse", "--git-common-dir").stdout).rstrip("\n")
    common = (project / common_output).resolve()
    key = hashlib.sha256(os.fsencode(common)).hexdigest()
    head_ref = os.fsdecode(git(project, "symbolic-ref", "-q", "HEAD", allowed=(0, 1)).stdout).rstrip("\n")
    if not head_ref.startswith("refs/heads/"):
        raise ValueError("the worktree must be on a local branch")
    head = git(project, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    return key, head_ref[len("refs/heads/"):], head


def check_location(store, project):
    if store == project or project in store.parents:
        raise ValueError("the two-pack store must be outside the project worktree")


def private(path):
    info = path.lstat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o077
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
        raise ValueError(f"use a private two-pack store owned by the current user: {path}")


def connect(store, *, initialize=False, read_only=False):
    path = store / "two-pack.sqlite3"
    if initialize:
        store.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(store)
    if initialize:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    private(path)
    mode = "ro" if read_only else "rw"
    db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def transaction(db, *, write=True):
    db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def get_run(db, run_id, expected_pack=None):
    # Existing databases keep their original schema until the first four-pack init.
    has_four = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='four_runs'").fetchone()
    if has_four:
        run = db.execute("SELECT runs.*, CASE WHEN f.run_id IS NULL THEN 'two' ELSE 'four' END AS pack "
                         "FROM runs LEFT JOIN four_runs f ON runs.id = f.run_id WHERE runs.id = ?",
                         (run_id,)).fetchone()
    else:
        run = db.execute("SELECT runs.*, 'two' AS pack FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or (expected_pack is not None and run["pack"] != expected_pack):
        raise ValueError(f"unknown {expected_pack or 'pack'} run: {run_id}")
    if run["pack_version"] != 1:
        raise ValueError(f"unsupported {run['pack']}-pack version")
    return run


def require_leader(run, identity):
    if identity() != run["leader"]:
        raise ValueError("this command requires the recorded Delivery Lead terminal")


def insert_card(db, run_id, version, body, checks, approval=None):
    timestamp = now()
    db.execute("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?)",
               (run_id, version, body, checks, timestamp, approval, timestamp if approval else None))


def get_card(db, run_id, version):
    return db.execute("SELECT * FROM cards WHERE run_id = ? AND version = ?",
                      (run_id, version)).fetchone()


def assignment_id(run, role, round_number):
    return f"{run['id']}/{role}/{round_number}"


def assignment_key(role, round_number):
    return f"{role}:{round_number}"


def insert_assignment(db, run, role, round_number, commit, source_receipt=None):
    key = assignment_key(role, round_number)
    item_id = assignment_id(run, role, round_number)
    target = json.loads(run["workers"])[role]
    db.execute("INSERT INTO assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
               (item_id, run["id"], role, round_number, target, commit, run["card_version"],
                now(), source_receipt))
    record_phase(db, run, "active", key)
    return item_id


def record_phase(db, run, phase, current):
    db.execute("UPDATE runs SET phase = ?, current = ?, updated_at = ? WHERE id = ?",
               (phase, current, now(), run["id"]))


def touch_run(db, run):
    db.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now(), run["id"]))


def get_assignment(db, item_id):
    return db.execute("SELECT * FROM assignments WHERE id = ?", (item_id,)).fetchone()


def current_assignment(db, run):
    if run["current"] is None:
        return None
    role, round_number = run["current"].split(":")
    return get_assignment(db, assignment_id(run, role, round_number))


def get_attempt(db, item_id):
    return db.execute("SELECT * FROM attempts WHERE assignment_id = ? ORDER BY number DESC LIMIT 1",
                      (item_id,)).fetchone()


def accepted_receipt(db, item_id):
    return db.execute("SELECT * FROM receipts WHERE assignment_id = ? AND accepted = 1",
                      (item_id,)).fetchone()


def attempt_view(row):
    view = {key: row[key] for key in ("number", "status", "intent_at", "finished_at", "inspected_at")}
    for key in ("outcome", "inspection"):
        if row[key]:
            view[key] = json.loads(row[key])
    return view


def receipt_view(row):
    view = {key: row[key] for key in ("id", "status", "worker", "received_at")}
    view["result"] = json.loads(row["payload"])
    if row["reason"]:
        view["reason"] = row["reason"]
    if row["notification"]:
        view["notification"] = json.loads(row["notification"])
    return view


def delivery_view(attempt, receipt):
    if attempt is None:
        return {"status": "pending"}
    view = attempt_view(attempt)
    if view["status"] in ("intent", "ambiguous"):
        if receipt:
            view["status"] = "received"
        else:
            view["action"] = "Inspect the original terminal and assignment before reconciling."
    return view


def dispatch_summary(db, item):
    receipt = accepted_receipt(db, item["id"])
    attempt = get_attempt(db, item["id"])
    delivery = delivery_view(attempt, receipt)
    return {"assignment": item["id"], "target": item["target"], "delivery": delivery,
            "receipt_saved": receipt is not None}, int(delivery["status"] in ("intent", "ambiguous"))


def check_dispatch_project(run, commit):
    project = Path(run["project"])
    project_id, branch, head = project_baseline(project)
    if (project_id, branch, head) != (run["project_id"], run["branch"], commit):
        raise ValueError("the worktree must be at the recorded project, branch and input commit")
    if git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none").stdout:
        raise ValueError("the worktree must be clean before dispatching the assigned role")
