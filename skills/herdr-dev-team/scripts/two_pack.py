"""Persist and dispatch serial two-pack runs."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import uuid

from pack_evidence import git, validate_checks


ROLES = {"coder": ("done", "blocked", "paused"),
         "cleaner": ("pass", "fail", "blocked", "paused")}
PACK_VERSION = 1
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


def read_checks(path):
    checks = json.loads(read_text(path))
    validate_checks(checks)
    return checks


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


def get_run(db, run_id):
    run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"unknown two-pack run: {run_id}")
    if run["pack_version"] != PACK_VERSION:
        raise ValueError("unsupported two-pack version")
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


def initialize(store, args, identity):
    leader = identity()
    workers = {role: getattr(args, role) for role in ROLES}
    if len({leader, *workers.values()}) != 3:
        raise ValueError("Delivery Lead, Coder and Cleaner need distinct local terminals")
    project = args.project.resolve(strict=True)
    check_location(store, project)
    project_id, branch, baseline = project_baseline(project)
    for value in (str(store), str(project), branch):
        guard_message(value)
    body = guard_message(read_text(args.card))
    checks = read_checks(args.checks)
    references = Path(__file__).resolve().parent.parent / "references"
    role_cards = {}
    for role in ROLES:
        path = references / f"two-{role}.md"
        role_cards[role] = {"path": str(path), "body": guard_message(read_text(path))}
    run_id = uuid.uuid4().hex
    timestamp = now()
    with closing(connect(store, initialize=True)) as db:
        db.executescript(SCHEMA)
        with transaction(db):
            existing = db.execute("SELECT project_id FROM runs LIMIT 1").fetchone()
            if existing and existing["project_id"] != project_id:
                raise ValueError("the two-pack store belongs to a different Git project")
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (run_id, str(project), project_id, branch, baseline, PACK_VERSION, leader,
                        encode(workers), args.rounds, "ready", None, 1, encode(role_cards),
                        timestamp, timestamp))
            insert_card(db, run_id, 1, body, encode(checks), "initial task card supplied at init")
    return {"id": run_id, "run": run_id, "directory": str(store), "database": str(store / "two-pack.sqlite3"),
            "phase": "ready", "branch": branch, "baseline": baseline, "card_version": 1}, 0


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


def resume(db, run):
    view = {key: run[key] for key in ("id", "project", "project_id", "branch", "baseline",
            "pack_version", "leader", "rounds", "phase", "current", "card_version", "created_at", "updated_at")}
    view.update(version=1, pack="two", workers=json.loads(run["workers"]),
                assignments={}, card_versions=[])
    for card in db.execute("SELECT * FROM cards WHERE run_id = ? ORDER BY version", (run["id"],)):
        entry = {key: card[key] for key in ("version", "body", "created_at", "approval", "approved_at")}
        entry["checks"] = json.loads(card["checks"])
        view["card_versions"].append(entry)
        if card["version"] == run["card_version"]:
            view.update(card=entry, checks=entry["checks"])
    items = db.execute("SELECT * FROM assignments WHERE run_id = ? ORDER BY round, role DESC", (run["id"],))
    for item in items:
        entry = {key: item[key] for key in ("id", "role", "round", "target", "input_commit",
                 "card_version", "created_at", "source_receipt")}
        entry["delivery"] = dispatch_summary(db, item)[0]["delivery"]
        entry["attempts"] = [attempt_view(row) for row in db.execute(
            "SELECT * FROM attempts WHERE assignment_id = ? ORDER BY number", (item["id"],))]
        entry["rejections"] = []
        for row in db.execute("SELECT * FROM receipts WHERE assignment_id = ? ORDER BY id", (item["id"],)):
            if row["accepted"]:
                entry["receipt"] = receipt_view(row)
            else:
                entry["rejections"].append(receipt_view(row))
        if item["decision"]:
            entry["decision"] = json.loads(item["decision"])
        view["assignments"][assignment_key(item["role"], item["round"])] = entry
    return view, 0


def assignment_message(db, store, run, item, number):
    card = get_card(db, run["id"], item["card_version"])
    role = item["role"]
    role_card = json.loads(run["role_cards"])[role]
    checks = json.loads(card["checks"])[role]
    command = ["python3", str(Path(__file__).resolve().with_name("handoff.py")), str(store),
               "two", run["id"], "report", role, str(item["round"])]
    source = ""
    if item["source_receipt"] is not None:
        prior = db.execute("SELECT status, payload FROM receipts WHERE id = ?",
                           (item["source_receipt"],)).fetchone()
        payload = json.dumps(json.loads(prior["payload"]), ensure_ascii=False, indent=2)
        source = f"\nPrior role receipt ({prior['status']}):\n{payload}\n"
    return guard_message(
        f"Herdr two-pack assignment {item['id']} (send attempt {number}).\n"
        f"Delivery Lead: {run['leader']}. Worktree: {run['project']}.\n"
        f"Branch: {run['branch']}. Baseline: {run['baseline']}. Input commit: {item['input_commit']}.\n"
        f"Private evidence directory: {store}.\n\n"
        f"Task card (version {card['version']}):\n{card['body']}\n"
        f"Frozen checks: {encode(checks)}\n{source}\n"
        f"Role instructions ({role_card['path']}):\n{role_card['body']}\n"
        f"Report with: {shlex.join(command)} STATUS --result RESULT_FILE\n"
        f"Use one of {', '.join(ROLES[role])} for STATUS and a UTF-8 JSON file for RESULT_FILE."
    )


@contextmanager
def dispatch_lock(store, item_id):
    # The hc child inherits this lock so inspection cannot race a surviving sender.
    name = "dispatch-" + item_id.replace("/", "-") + ".lock"
    with (store / name).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield lock.fileno()


def dispatch(db, store, run, item_id, send, *, recover=False, inspection=None):
    item = get_assignment(db, item_id)
    if item is None:
        raise ValueError("unknown assignment")
    with dispatch_lock(store, item_id) as fd:
        with transaction(db):
            latest = get_attempt(db, item_id)
            if fd is None:
                if inspection:
                    raise ValueError("the original dispatch may still be in progress; inspect before reconciling")
                return dispatch_summary(db, item)
            receipt = accepted_receipt(db, item_id)
            if inspection:
                current = current_assignment(db, get_run(db, run["id"]))
                if (current is None or current["id"] != item_id or receipt or latest is None
                        or latest["status"] not in ("intent", "ambiguous")
                        or latest["number"] != inspection["attempt"]):
                    raise ValueError("that send attempt no longer needs reconciliation")
                delivered = inspection["decision"] == "delivered"
                decision = "delivered-by-inspection" if delivered else "not-delivered-by-inspection"
                db.execute("UPDATE attempts SET status = ?, inspection = ?, inspected_at = ? "
                           "WHERE assignment_id = ? AND number = ?",
                           (decision, encode(inspection), now(), item_id, latest["number"]))
                if delivered:
                    touch_run(db, run)
                    return dispatch_summary(db, item)
            elif receipt or latest:
                if recover and not receipt and latest["status"] == "intent":
                    outcome = {"status": "ambiguous", "error": "recovered attempt has no recorded send result"}
                    db.execute("UPDATE attempts SET status = 'ambiguous', outcome = ? "
                               "WHERE assignment_id = ? AND number = ?",
                               (encode(outcome), item_id, latest["number"]))
                    touch_run(db, run)
                return dispatch_summary(db, item)

            check_dispatch_project(run, item["input_commit"])
            number = latest["number"] + 1 if latest else 1
            message = assignment_message(db, store, run, item, number)
            db.execute("INSERT INTO attempts (assignment_id, number, status, intent_at) VALUES (?, ?, ?, ?)",
                       (item_id, number, "intent", now()))
            touch_run(db, run)

        outcome = send(item["target"], message, fd)
        status = "delivered" if outcome.get("status") == "delivered" else "ambiguous"
        with transaction(db):
            db.execute("UPDATE attempts SET status = ?, finished_at = ?, outcome = ? "
                       "WHERE assignment_id = ? AND number = ?", (status, now(), encode(outcome), item_id, number))
            touch_run(db, run)
            return dispatch_summary(db, item)


def start(db, store, run, send):
    with transaction(db):
        run = get_run(db, run["id"])
        item = get_assignment(db, assignment_id(run, "coder", 1))
        if item:
            return dispatch_summary(db, item)
        if run["phase"] != "ready":
            raise ValueError("run is not ready to start")
        check_dispatch_project(run, run["baseline"])
        item_id = insert_assignment(db, run, "coder", 1, run["baseline"])
    return dispatch(db, store, run, item_id, send)


def check_dispatch_project(run, commit):
    project = Path(run["project"])
    project_id, branch, head = project_baseline(project)
    if (project_id, branch, head) != (run["project_id"], run["branch"], commit):
        raise ValueError("the worktree must be at the recorded project, branch and input commit")
    if git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none").stdout:
        raise ValueError("the worktree must be clean before dispatching the assigned role")


def advance(db, run, item, status, payload, receipt_id):
    phase, following = status, None
    number = item["round"]
    if status == "done":
        phase, following = "active", ("cleaner", number)
    elif status == "pass":
        phase = "completed"
    elif status == "fail":
        if number < run["rounds"]:
            phase, following = "active", ("coder", number + 1)
        else:
            phase = "rework-exhausted"
    decision = {"phase": phase, "decided_at": now(), "receipt": receipt_id}
    if following:
        next_id = insert_assignment(db, run, *following, payload["commit"], receipt_id)
        decision["next"] = assignment_key(*following)
    else:
        current = None if phase == "completed" else assignment_key(item["role"], number)
        record_phase(db, run, phase, current)
        next_id = None
    db.execute("UPDATE assignments SET decision = ? WHERE id = ?", (encode(decision), item["id"]))
    return decision, next_id


def report(db, store, run, args, identity, send, validate):
    worker = identity()
    payload = json.loads(read_text(args.result))
    submitted = encode(payload)
    with transaction(db):
        run = get_run(db, run["id"])
        item = get_assignment(db, assignment_id(run, args.role, args.round))
        if item is None:
            raise ValueError("unknown assignment")
        if worker != item["target"]:
            raise ValueError("this command requires the assigned worker terminal")
        prior = accepted_receipt(db, item["id"])
        if prior and prior["status"] == args.status and prior["submitted"] == submitted:
            result = {"assignment": item["id"], "receipt_saved": True, "duplicate": True,
                      **json.loads(item["decision"])}
            if "next" in result:
                role, round_number = result["next"].split(":")
                successor = get_assignment(db, assignment_id(run, role, round_number))
                result["dispatch"], code = dispatch_summary(db, successor)
                return result, code
            return result, 0
        received_at = now()
        rejection = None
        try:
            if prior:
                raise ValueError("conflicting receipt; the accepted receipt was kept")
            if run["current"] != assignment_key(args.role, args.round):
                raise ValueError("wrong or stale assignment")
            if get_attempt(db, item["id"]) is None:
                raise ValueError("assignment has not been dispatched")
            if args.status not in ROLES[args.role]:
                raise ValueError(f"invalid {args.role} status; use {', '.join(ROLES[args.role])}")
            card = get_card(db, run["id"], item["card_version"])
            validate(Path(run["project"]), run["branch"], item["input_commit"], args.role,
                     args.status, payload, store, json.loads(card["checks"]))
        except ValueError as exc:
            rejection = str(exc)
        cursor = db.execute("INSERT INTO receipts (assignment_id, status, worker, received_at, submitted, "
                            "payload, accepted, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (item["id"], args.status, worker, received_at, submitted,
                             encode(payload) if rejection is None else submitted, int(rejection is None), rejection))
        if rejection is not None:
            touch_run(db, run)
            return {"assignment": item["id"], "receipt_saved": False, "rejection": rejection}, 1
        receipt_id = cursor.lastrowid
        decision, next_id = advance(db, run, item, args.status, payload, receipt_id)

    result = {"assignment": item["id"], "receipt_saved": True, **decision}
    code = 0
    if next_id:
        result["dispatch"], code = dispatch(db, store, run, next_id, send)
    result["notification"] = notify(db, store, run, item, args.status, decision, receipt_id, send)
    return result, code


def notify(db, store, run, item, status, decision, receipt_id, send):
    command = ["python3", str(Path(__file__).resolve().with_name("handoff.py")), str(store),
               "two", run["id"], "resume"]
    message = (f"Herdr two-pack receipt saved for {item['id']} ({status}). "
               f"Decision: {decision['phase']}. Inspect with {shlex.join(command)}.")
    sent_at = now()
    outcome = send(run["leader"], guard_message(message))
    notification = {"sent_at": sent_at, "finished_at": now(), **outcome}
    with transaction(db):
        db.execute("UPDATE receipts SET notification = ? WHERE id = ?", (encode(notification), receipt_id))
        touch_run(db, run)
    return notification


def revise(db, store, run, args, send):
    body = guard_message(read_text(args.card))
    new_checks = read_checks(args.checks) if args.checks else None
    approval = nonempty(args.approve_evidence, "approval evidence") if args.approve_evidence is not None else None
    with transaction(db):
        run = get_run(db, run["id"])
        active = get_card(db, run["id"], run["card_version"])
        newest = db.execute("SELECT * FROM cards WHERE run_id = ? ORDER BY version DESC LIMIT 1",
                            (run["id"],)).fetchone()
        inherited = newest if newest["body"] == body else active
        checks = encode(new_checks) if new_checks is not None else inherited["checks"]
        same_card = (newest["body"], newest["checks"]) == (body, checks)
        version = newest["version"] if same_card else newest["version"] + 1
        if not same_card:
            insert_card(db, run["id"], version, body, checks)
            touch_run(db, run)
        if not approval or version == run["card_version"]:
            return {"run": run["id"], "card_version": version, "active": version == run["card_version"]}, 0
        if run["phase"] not in ("ready", "paused", "blocked"):
            raise ValueError("activate a revised task card before dispatch or after the assigned worker pauses or blocks")
        db.execute("UPDATE cards SET approval = ?, approved_at = ? WHERE run_id = ? AND version = ?",
                   (approval, now(), run["id"], version))
        db.execute("UPDATE runs SET card_version = ?, updated_at = ? WHERE id = ?", (version, now(), run["id"]))
        item_id = None
        if run["phase"] != "ready":
            last = current_assignment(db, run)
            round_number = last["round"] + 1
            if round_number > run["rounds"]:
                raise ValueError("the round budget is exhausted; initialize a new run for the revised card")
            receipt = accepted_receipt(db, last["id"])
            commit = json.loads(receipt["payload"])["commit"]
            check_dispatch_project(run, commit)
            item_id = insert_assignment(db, get_run(db, run["id"]), "coder", round_number, commit, receipt["id"])
    result = {"run": run["id"], "card_version": version, "active": True}
    if item_id:
        result["dispatch"], code = dispatch(db, store, run, item_id, send)
        return result, code
    return result, 0


def terminal(value):
    if not re.fullmatch(r"t[0-9a-z]+", value):
        raise argparse.ArgumentTypeError("use a literal local Herdr terminal ID (for example, t7)")
    return value


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def init_parser():
    cli = argparse.ArgumentParser(prog="handoff.py STORE two init")
    cli.add_argument("--project", required=True, type=Path)
    cli.add_argument("--card", required=True, type=Path)
    cli.add_argument("--checks", required=True, type=Path)
    for role in ROLES:
        cli.add_argument(f"--{role}", required=True, type=terminal)
    cli.add_argument("--rounds", type=positive, default=5)
    return cli


def run_parser():
    cli = argparse.ArgumentParser(prog="handoff.py STORE two")
    cli.add_argument("run_id", help="run ID, or init to record a new run")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("resume", help="read the run without writing or sending")
    commands.add_parser("start", help="queue the initial Coder assignment")
    commands.add_parser("recover", help="inspect or claim the original assignment")
    receipt = commands.add_parser("report", help="submit a role receipt and advance automatically")
    reconcile = commands.add_parser("reconcile", help="record inspection of an uncertain send")
    for command in (receipt, reconcile):
        command.add_argument("role", choices=ROLES)
        command.add_argument("round", type=positive)
    receipt.add_argument("status", choices=sorted({status for statuses in ROLES.values() for status in statuses}))
    receipt.add_argument("--result", required=True, type=Path)
    reconcile.add_argument("attempt", type=positive)
    reconcile.add_argument("decision", choices=("delivered", "retry"),
                           help="retry only if inspection proves the original send cannot arrive")
    reconcile.add_argument("--evidence", required=True)
    revision = commands.add_parser("revise", help="record a revision and optionally activate it with approval")
    revision.add_argument("--card", required=True, type=Path)
    revision.add_argument("--checks", type=Path)
    revision.add_argument("--approve-evidence")
    return cli


def main(store, argv, *, identity, send, validate):
    store = Path(store).resolve()
    try:
        if argv and argv[0] == "init":
            return initialize(store, init_parser().parse_args(argv[1:]), identity)
        args = run_parser().parse_args(argv)
        with closing(connect(store, read_only=args.command == "resume")) as db:
            if args.command == "resume":
                with transaction(db, write=False):
                    return resume(db, get_run(db, args.run_id))
            run = get_run(db, args.run_id)
            check_location(store, Path(run["project"]))
            if args.command == "report":
                return report(db, store, run, args, identity, send, validate)
            require_leader(run, identity)
            if args.command == "start":
                return start(db, store, run, send)
            if args.command == "revise":
                return revise(db, store, run, args, send)
            if args.command == "reconcile":
                evidence = nonempty(args.evidence, "inspection evidence")
                inspection = {"attempt": args.attempt, "decision": args.decision,
                              "evidence": evidence, "leader": run["leader"]}
                return dispatch(db, store, run, assignment_id(run, args.role, args.round), send,
                                inspection=inspection)
            item = current_assignment(db, run)
            if item is None:
                return {"run": run["id"], "phase": run["phase"], "current": None}, 0
            return dispatch(db, store, run, item["id"], send, recover=True)
    except sqlite3.Error as exc:
        raise ValueError(f"two-pack database: {exc}") from exc
