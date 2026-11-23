"""Dispatch and recover serial engineering packs."""

import argparse
from contextlib import closing, contextmanager
import fcntl
import json
from pathlib import Path
import re
import shlex
import sqlite3
import uuid

import four_pack
from pack_evidence import validate_checks
from pack_store import (
    SCHEMA, accepted_receipt, assignment_id, assignment_key, attempt_view, check_dispatch_project,
    check_location, connect, dispatch_summary, encode, get_assignment, get_attempt, get_card,
    get_run, guard_message, insert_assignment, insert_card, nonempty, now, project_baseline,
    read_text, receipt_view, record_phase, require_leader, touch_run, transaction, current_assignment,
)
from spec_artifact import spec_path


ROLES = {"coder": ("done", "blocked", "paused"),
         "cleaner": ("pass", "fail", "blocked", "paused")}
PACK_VERSION = 1


def roles(pack):
    return four_pack.ROLES if pack == "four" else ROLES


def read_checks(path, pack):
    checks = json.loads(read_text(path))
    validate_checks(checks, pack=pack)
    return checks


def initialize(store, args, identity, pack):
    leader = identity()
    workers = {role: getattr(args, role) for role in roles(pack)}
    if len({leader, *workers.values()}) != len(workers) + 1:
        if pack == "two":
            raise ValueError("Delivery Lead, Coder and Cleaner need distinct local terminals")
        raise ValueError("Delivery Lead and all four workers need distinct local terminals")
    examples = spec_path(args.examples) if pack == "four" else None
    project = args.project.resolve(strict=True)
    check_location(store, project)
    project_id, branch, baseline = project_baseline(project)
    for value in (str(store), str(project), branch):
        guard_message(value)
    body = guard_message(read_text(args.card))
    checks = read_checks(args.checks, pack)
    references = Path(__file__).resolve().parent.parent / "references"
    role_cards = {}
    for role in workers:
        path = references / f"{pack}-{role}.md"
        role_cards[role] = {"path": str(path), "body": guard_message(read_text(path))}
    run_id = uuid.uuid4().hex
    timestamp = now()
    with closing(connect(store, initialize=True)) as db:
        db.executescript(SCHEMA + (four_pack.SCHEMA if examples else ""))
        with transaction(db):
            existing = db.execute("SELECT project_id FROM runs LIMIT 1").fetchone()
            if existing and existing["project_id"] != project_id:
                raise ValueError("the two-pack store belongs to a different Git project")
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (run_id, str(project), project_id, branch, baseline, PACK_VERSION, leader,
                        encode(workers), getattr(args, "rounds", 0), "ready", None, 1, encode(role_cards),
                        timestamp, timestamp))
            if examples:
                db.execute("INSERT INTO four_runs VALUES (?, ?)", (run_id, examples))
            insert_card(db, run_id, 1, body, encode(checks), "initial task card supplied at init")
    return {"id": run_id, "run": run_id, "directory": str(store), "database": str(store / "two-pack.sqlite3"),
            "phase": "ready", "branch": branch, "baseline": baseline, "card_version": 1}, 0


def resume_summary(view):
    phase, current, items = view["phase"], view["current"], view["assignments"]
    if phase == "ready":
        return "等待向 Coder 派工。"
    if phase == "completed":
        item = items[next(reversed(items))]
        receipt, decision = item.get("receipt", {}), item.get("decision", {})
        # Report acceptance already checked the commit and snapshotted the required evidence.
        if (item["role"] == "cleaner" and receipt.get("status") == "pass"
                and decision.get("phase") == "completed" and decision.get("receipt") == receipt["id"]):
            commit = receipt["result"]["commit"]
            return f"第 {item['round']} 轮，Cleaner 已在 {commit[:7]} 验收通过，two-pack 已完成。"
        return "完成记录待核实。"

    item = items[current]
    role, number = item["role"].capitalize(), item["round"]
    prefix = f"第 {number} 轮，"
    if phase == "paused":
        return f"{prefix}{role} 已暂停。"
    if phase == "blocked":
        return f"{prefix}{role} 遇阻。"
    if phase == "rework-exhausted":
        return f"{prefix}Cleaner 验收未通过，返工轮次已用尽。"

    delivery = item["delivery"]["status"]
    if role == "Cleaner":
        coder = items[assignment_key("coder", number)]
        commit = coder["receipt"]["result"]["commit"]
        prefix += f"Coder 已交付 {commit[:7]}："
        if delivery in ("delivered", "delivered-by-inspection"):
            return f"{prefix}已向 Cleaner 派工，尚无 Cleaner 完成回执。"
    else:
        previous = items.get(assignment_key("cleaner", number - 1), {})
        if (previous.get("receipt", {}).get("status") == "fail"
                and previous.get("decision", {}).get("next") == current):
            prefix += "Cleaner 验收未通过，"
        if delivery in ("delivered", "delivered-by-inspection"):
            return f"{prefix}已向 Coder 派工，尚无 Coder 完成回执。"
    if delivery == "pending":
        return f"{prefix}等待向 {role} 派工。"
    return f"{prefix}给 {role} 的派工送达待核实。"


def resume(db, run):
    view = {key: run[key] for key in ("id", "project", "project_id", "branch", "baseline",
            "pack_version", "leader", "rounds", "phase", "current", "card_version", "created_at", "updated_at")}
    pack = run["pack"]
    view.update(version=1, pack=pack, workers=json.loads(run["workers"]),
                assignments={}, card_versions=[])
    if pack == "four":
        del view["rounds"]
    for card in db.execute("SELECT * FROM cards WHERE run_id = ? ORDER BY version", (run["id"],)):
        entry = {key: card[key] for key in ("version", "body", "created_at", "approval", "approved_at")}
        entry["checks"] = json.loads(card["checks"])
        view["card_versions"].append(entry)
        if card["version"] == run["card_version"]:
            view.update(card=entry, checks=entry["checks"])
    order = "round, role DESC" if pack == "two" else "rowid"
    items = db.execute(f"SELECT * FROM assignments WHERE run_id = ? ORDER BY {order}", (run["id"],))
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
    view["summary"] = four_pack.resume(db, run, view) if pack == "four" else resume_summary(view)
    return view, 0


def assignment_message(db, store, run, item, number):
    card = get_card(db, run["id"], item["card_version"])
    role, pack = item["role"], run["pack"]
    role_card = json.loads(run["role_cards"])[role]
    checks = json.loads(card["checks"])[role]
    command = ["python3", str(Path(__file__).resolve().with_name("handoff.py")), str(store),
               pack, run["id"], "report", role, str(item["round"])]
    context = four_pack.assignment_context(db, run, item) if pack == "four" else ""
    source = ""
    if item["source_receipt"] is not None:
        prior = db.execute("SELECT status, payload FROM receipts WHERE id = ?",
                           (item["source_receipt"],)).fetchone()
        payload = json.dumps(json.loads(prior["payload"]), ensure_ascii=False, indent=2)
        source = f"\nPrior role receipt ({prior['status']}):\n{payload}\n"
    return guard_message(
        f"Herdr {pack}-pack assignment {item['id']} (send attempt {number}).\n"
        f"Delivery Lead: {run['leader']}. Worktree: {run['project']}.\n"
        f"Branch: {run['branch']}. Baseline: {run['baseline']}. Input commit: {item['input_commit']}.\n"
        f"Private evidence directory: {store}.\n\n"
        f"Task card (version {card['version']}):\n{card['body']}\n"
        f"Frozen checks: {encode(checks)}\n{context}{source}\n"
        f"Role instructions ({role_card['path']}):\n{role_card['body']}\n"
        f"Report with: {shlex.join(command)} STATUS --result RESULT_FILE\n"
        f"Use one of {', '.join(roles(pack)[role])} for STATUS and a UTF-8 JSON file for RESULT_FILE."
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
                run = get_run(db, run["id"])
                current = current_assignment(db, run)
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
                if run["pack"] == "four":
                    changed = four_pack.changed_examples(db, run)
                    if changed:
                        replacement = four_pack.retire_changed_examples(db, run, changed)
                        result, code = dispatch_summary(db, item)
                        return dict(result, replacement=replacement), code
            elif receipt or latest:
                if recover and not receipt and latest["status"] == "intent":
                    outcome = {"status": "ambiguous", "error": "recovered attempt has no recorded send result"}
                    db.execute("UPDATE attempts SET status = 'ambiguous', outcome = ? "
                               "WHERE assignment_id = ? AND number = ?",
                               (encode(outcome), item_id, latest["number"]))
                    touch_run(db, run)
                return dispatch_summary(db, item)

            if run["pack"] == "four" and get_run(db, run["id"])["current"] != assignment_key(
                    item["role"], item["round"]):
                return {"assignment": item_id, "delivery": {"status": "superseded"}}, 0
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
        first = "specifier" if run["pack"] == "four" else "coder"
        item = get_assignment(db, assignment_id(run, first, 1))
        if item:
            return dispatch_summary(db, item)
        if run["phase"] != "ready":
            raise ValueError("run is not ready to start")
        check_dispatch_project(run, run["baseline"])
        item_id = insert_assignment(db, run, first, 1, run["baseline"])
    return dispatch(db, store, run, item_id, send)


def two_transition(run, number, status):
    if status == "done":
        return "active", ("cleaner", number)
    if status == "pass":
        return "completed", None
    if status == "fail":
        if number < run["rounds"]:
            return "active", ("coder", number + 1)
        return "rework-exhausted", None
    return status, None


def advance(db, run, item, status, payload, receipt_id, artifact):
    number = item["round"]
    if run["pack"] == "four":
        phase, following = four_pack.transition(db, run, item, status, receipt_id, artifact)
    else:
        phase, following = two_transition(run, number, status)
    decision = {"phase": phase, "decided_at": now(), "receipt": receipt_id}
    if following:
        next_id = insert_assignment(db, run, *following, payload["commit"], receipt_id)
        decision["next"] = assignment_key(*following)
    else:
        current = None if phase in ("completed", "waiting-for-human") else assignment_key(item["role"], number)
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
        rejection, artifact = None, None
        pack = run["pack"]
        try:
            if prior:
                raise ValueError("conflicting receipt; the accepted receipt was kept")
            if run["current"] != assignment_key(args.role, args.round):
                raise ValueError("wrong or stale assignment")
            if get_attempt(db, item["id"]) is None:
                raise ValueError("assignment has not been dispatched")
            statuses = roles(pack)[args.role]
            if args.status not in statuses:
                raise ValueError(f"invalid {args.role} status; use {', '.join(statuses)}")
            card = get_card(db, run["id"], item["card_version"])
            checks = json.loads(card["checks"])
            if pack == "four":
                changed = four_pack.changed_examples(db, run)
                if args.status == "superseded" and not changed:
                    raise ValueError("superseded requires a committed change to the examples")
                if changed and args.status not in ("superseded", "paused", "blocked"):
                    raise ValueError(four_pack.YIELD_ACTION)
                artifact = four_pack.validate_receipt(db, store, run, item, args.status, payload, checks, validate)
                if args.status == "superseded":
                    if payload["commit"] != changed:
                        raise ValueError("receipt must match the clean HEAD where the example change was detected")
                    check_dispatch_project(run, changed)
            else:
                validate(Path(run["project"]), run["branch"], item["input_commit"], args.role,
                         args.status, payload, store, checks)
        except ValueError as exc:
            rejection = str(exc)
        cursor = db.execute("INSERT INTO receipts (assignment_id, status, worker, received_at, submitted, "
                            "payload, accepted, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (item["id"], args.status, worker, received_at, submitted,
                             encode(payload) if rejection is None else submitted, int(rejection is None), rejection))
        if rejection is not None:
            touch_run(db, run)
        else:
            receipt_id = cursor.lastrowid
            decision, next_id = advance(db, run, item, args.status, payload, receipt_id, artifact)

    if rejection is not None:
        return {"assignment": item["id"], "receipt_saved": False, "rejection": rejection}, 1
    result = {"assignment": item["id"], "receipt_saved": True, **decision}
    code = 0
    if next_id:
        result["dispatch"], code = dispatch(db, store, run, next_id, send)
    result["notification"] = notify(db, store, run, item, args.status, decision, receipt_id, send)
    return result, code


def notify(db, store, run, item, status, decision, receipt_id, send):
    pack = run["pack"]
    command = ["python3", str(Path(__file__).resolve().with_name("handoff.py")), str(store),
               pack, run["id"], "resume"]
    message = (f"Herdr {pack}-pack receipt saved for {item['id']} ({status}). "
               f"Decision: {decision['phase']}. Inspect with {shlex.join(command)}.")
    sent_at = now()
    outcome = send(run["leader"], guard_message(message))
    notification = {"sent_at": sent_at, "finished_at": now(), **outcome}
    with transaction(db):
        db.execute("UPDATE receipts SET notification = ? WHERE id = ?", (encode(notification), receipt_id))
        touch_run(db, run)
    return notification


def revise(db, store, run, args, send):
    pack = run["pack"]
    body = guard_message(read_text(args.card))
    new_checks = read_checks(args.checks, pack) if args.checks else None
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
        allowed_phases = ("ready", "paused", "blocked")
        if pack == "four":
            allowed_phases += ("waiting-for-human",)
        if run["phase"] not in allowed_phases:
            raise ValueError("activate a revised task card before dispatch or after the assigned worker pauses or blocks")
        db.execute("UPDATE cards SET approval = ?, approved_at = ? WHERE run_id = ? AND version = ?",
                   (approval, now(), run["id"], version))
        db.execute("UPDATE runs SET card_version = ?, updated_at = ? WHERE id = ?", (version, now(), run["id"]))
        item_id = None
        if run["phase"] != "ready" and pack == "four":
            item_id = four_pack.rework(db, get_run(db, run["id"]), specification=True)
        elif run["phase"] != "ready":
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


def init_parser(pack):
    cli = argparse.ArgumentParser(prog=f"handoff.py STORE {pack} init")
    cli.add_argument("--project", required=True, type=Path)
    cli.add_argument("--card", required=True, type=Path)
    cli.add_argument("--checks", required=True, type=Path)
    for role in roles(pack):
        cli.add_argument(f"--{role}", required=True, type=terminal)
    if pack == "four":
        cli.add_argument("--examples", required=True, help="committed project-relative examples path")
    else:
        cli.add_argument("--rounds", type=positive, default=5)
    return cli


def run_parser(pack):
    cli = argparse.ArgumentParser(prog=f"handoff.py STORE {pack}")
    cli.add_argument("run_id", help="run ID, or init to record a new run")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("resume", help="read the run without writing or sending")
    first = "Specifier" if pack == "four" else "Coder"
    commands.add_parser("start", help=f"queue the initial {first} assignment")
    commands.add_parser("recover", help="inspect or claim the original assignment")
    receipt = commands.add_parser("report", help="submit a role receipt and advance automatically")
    reconcile = commands.add_parser("reconcile", help="record inspection of an uncertain send")
    for command in (receipt, reconcile):
        command.add_argument("role", choices=roles(pack))
        command.add_argument("round", type=positive)
    receipt.add_argument("status", choices=sorted({status for statuses in roles(pack).values() for status in statuses}))
    receipt.add_argument("--result", required=True, type=Path)
    reconcile.add_argument("attempt", type=positive)
    reconcile.add_argument("decision", choices=("delivered", "retry"),
                           help="retry only if inspection proves the original send cannot arrive")
    reconcile.add_argument("--evidence", required=True)
    revision = commands.add_parser("revise", help="record a revision and optionally activate it with approval")
    revision.add_argument("--card", required=True, type=Path)
    revision.add_argument("--checks", type=Path)
    revision.add_argument("--approve-evidence")
    if pack == "four":
        four_pack.attach_parser(commands, positive)
    return cli


def four_command(db, store, run, args, send):
    with transaction(db):
        run = get_run(db, run["id"])
        if args.command == "approve":
            result, item_id = four_pack.approve(db, run, args.version, args.sha256, args.evidence)
            if item_id is None:
                return result, 1
        else:
            changed = four_pack.changed_examples(db, run)
            if not changed and args.command != "rework":
                return None
            if changed:
                item_id = four_pack.retire_changed_examples(db, run, changed)
                if item_id is None:
                    if args.command != "recover":
                        raise ValueError(four_pack.YIELD_ACTION)
                    item_id = current_assignment(db, run)["id"]
                    result = {"run": run["id"], "phase": run["phase"], "action": four_pack.YIELD_ACTION}
                else:
                    result = {"run": run["id"], "phase": "active"}
            else:
                item_id = four_pack.rework(db, run, specification=args.specification)
                result = {"run": run["id"], "phase": "active"}
    result["dispatch"], code = dispatch(db, store, run, item_id, send, recover=args.command == "recover")
    return result, 1 if result.get("approved") is False else code


def main(store, argv, *, identity, send, validate, pack="two"):
    store = Path(store).resolve()
    try:
        if argv and argv[0] == "init":
            return initialize(store, init_parser(pack).parse_args(argv[1:]), identity, pack)
        args = run_parser(pack).parse_args(argv)
        with closing(connect(store, read_only=args.command == "resume")) as db:
            if args.command == "resume":
                with transaction(db, write=False):
                    return resume(db, get_run(db, args.run_id, pack))
            run = get_run(db, args.run_id, pack)
            check_location(store, Path(run["project"]))
            if args.command == "report":
                return report(db, store, run, args, identity, send, validate)
            require_leader(run, identity)
            if args.command == "start":
                return start(db, store, run, send)
            if args.command == "revise":
                return revise(db, store, run, args, send)
            if pack == "four" and args.command != "reconcile":
                result = four_command(db, store, run, args, send)
                if result is not None:
                    return result
                run = get_run(db, args.run_id, pack)
            if args.command == "reconcile":
                evidence = nonempty(args.evidence, "inspection evidence")
                inspection = {"attempt": args.attempt, "decision": args.decision,
                              "evidence": evidence, "leader": run["leader"]}
                result, code = dispatch(db, store, run, assignment_id(run, args.role, args.round), send,
                                        inspection=inspection)
                if result.get("replacement"):
                    result["dispatch"], code = dispatch(db, store, run, result["replacement"], send)
                return result, code
            item = current_assignment(db, run)
            if item is None:
                return {"run": run["id"], "phase": run["phase"], "current": None}, 0
            return dispatch(db, store, run, item["id"], send, recover=True)
    except sqlite3.Error as exc:
        raise ValueError(f"{pack}-pack database: {exc}") from exc
