#!/usr/bin/env python3
"""Save and deliver Herdr Dev Team handoffs to prepared local terminals."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import uuid


ROLES = {"coder": ("done", "blocked"), "reviewer": ("pass", "fail", "blocked")}


class HandoffError(Exception):
    pass


def terminal(value):
    if not re.fullmatch(r"t[0-9a-z]+", value):
        raise ValueError("use a literal local Herdr terminal ID (for example, t7)")
    return value


def identity():
    return terminal(os.environ.get("HC_TERMINAL_ID", ""))


def positive(value):
    number = int(value)
    if number < 1:
        raise ValueError("must be positive")
    return number


def text_file(path):
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise HandoffError(f"empty file: {path}")
    return text


class Run:
    def __init__(self, path):
        self.path = path.resolve()
        self.record = self.path / "state.json"

    @contextmanager
    def lock(self, name="state", nonblocking=False):
        if not self.path.is_dir():
            raise HandoffError("run directory does not exist; use init")
        with (self.path / f"{name}.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
            except BlockingIOError:
                raise HandoffError("a Leader dispatch is in progress; use resume to inspect") from None
            yield lock.fileno()

    def load(self):
        if not self.record.exists():
            raise HandoffError("run is not initialized; use init")
        state = json.loads(self.record.read_text(encoding="utf-8"))
        if state.get("version") != 1:
            raise HandoffError("unsupported run state version")
        return state

    def save(self, state):
        # Commit before external effects; the lock lives on a separate inode.
        fd, name = tempfile.mkstemp(prefix="state-", dir=self.path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.record)
            directory = os.open(self.path, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def send(target, message, dispatch_fd=None):
    # A surviving hc client keeps the dispatch lock if the Leader command dies.
    keep = () if dispatch_fd is None else (dispatch_fd,)
    try:
        result = subprocess.run(
            ["hc", "send", "--json", target, message], pass_fds=keep,
            capture_output=True, text=True, errors="replace", timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "ambiguous", "error": str(exc) if isinstance(exc, OSError)
                else "hc send timed out; delivery is unknown"}
    if result.returncode == 0:
        try:
            reply = json.loads(result.stdout)
        except ValueError:
            reply = None
        if isinstance(reply, dict) and type(reply.get("turn")) is int and reply["turn"] >= 0:
            return {"status": "delivered", "hc": reply}
    return {"status": "ambiguous", "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def assignment_key(role, round_number):
    return f"{role}:{round_number}"


def assignment_message(run, state, item):
    role, number = item["role"], item["round"]
    command = ["python3", str(Path(__file__).resolve()), str(run.path), "report", role, str(number)]
    report = " ".join(map(shlex.quote, command))
    allowed = ", ".join(ROLES[role])
    return (
        f"Herdr Dev Team assignment {item['id']} (send attempt {len(item['attempts'])}).\n"
        f"Leader: {state['leader']}. Project: {state['project']}.\n"
        f"{item['brief']}\n\n"
        f"When finished or blocked, write your evidence to a result file. Run:\n"
        f"{report} STATUS --result RESULT_FILE\n"
        f"Replace STATUS with one of {allowed}, and RESULT_FILE with its path. "
        "This saves the receipt before notifying the Leader. End your turn after reporting."
    )


def new_assignment(state, role, number, brief):
    key = assignment_key(role, number)
    item = {"id": f"{state['id']}/{role}/{number}", "role": role, "round": number,
            "target": state["workers"][role], "brief": brief, "attempts": []}
    state["assignments"][key] = item
    state["current"] = key
    return item


def start_intent(run, state, item):
    item["attempts"].append({"status": "intent"})
    message = assignment_message(run, state, item)
    if any(not ch.isprintable() and ch not in "\n\t" for ch in message):
        raise HandoffError("assignment text or path contains terminal control characters")
    run.save(state)
    return message


def finish_send(run, item, message, dispatch_fd):
    outcome = send(item["target"], message, dispatch_fd)
    # Reload: the worker can save a receipt while hc is returning.
    with run.lock():
        state = run.load()
        latest = state["assignments"][assignment_key(item["role"], item["round"])]
        latest["attempts"][-1].update(outcome)
        run.save(state)
        return dispatch_summary(latest)


def dispatch_summary(item):
    attempt = item["attempts"][-1]
    result = {"assignment": item["id"], "target": item["target"],
              "attempt": len(item["attempts"]), "delivery": attempt,
              "receipt_saved": "receipt" in item}
    unresolved = attempt["status"] in ("intent", "ambiguous") and "receipt" not in item
    if unresolved:
        result["action"] = "Inspect the original terminal before using reconcile. Do not resend blindly."
    return result, int(unresolved)


def initialize(run, args):
    if not args.confirm:
        raise HandoffError("confirm the effective configuration with the user before using --confirm")
    leader = identity()
    if len({leader, args.coder, args.reviewer}) != 3:
        raise HandoffError("Leader, Coder and Reviewer need distinct local terminals")
    project = args.project.resolve(strict=True)
    if not project.is_dir():
        raise HandoffError("project must be a directory")
    config = dict(version=1, project=str(project), leader=leader,
                  workers={"coder": args.coder, "reviewer": args.reviewer},
                  configuration=text_file(args.config), rounds=args.rounds)
    run.path.mkdir(mode=0o700, exist_ok=True)
    with run.lock():
        if run.record.exists():
            state = run.load()
            if any(state[key] != value for key, value in config.items()):
                raise HandoffError("this run is already initialized with different settings")
        else:
            state = dict(config, id=uuid.uuid4().hex, phase="ready", current=None, assignments={})
            run.save(state)
        return {"run": state["id"], "directory": str(run.path)}, 0


def require_leader(state):
    if identity() != state["leader"]:
        raise HandoffError("this command requires the recorded Leader terminal")


def next_step(state, item):
    receipt = item["receipt"]["status"]
    number = item["round"]
    if receipt == "blocked":
        return "blocked", None
    if item["role"] == "coder":
        return "active", ("reviewer", number)
    if receipt == "pass":
        return "qa-ready", None
    if number == state["rounds"]:
        return "review-exhausted", None
    return "active", ("coder", number + 1)


def advance(run, args):
    with run.lock("dispatch", nonblocking=True) as dispatch_fd:
        with run.lock():
            state = run.load()
            require_leader(state)
            if args.command == "start":
                existing = state["assignments"].get("coder:1")
                if existing:
                    return repeated_dispatch(args, existing)
                brief = text_file(args.brief)
                item = new_assignment(state, "coder", 1, brief)
                state["phase"] = "active"
            else:
                key = assignment_key(args.role, args.round)
                item = state["assignments"].get(key)
                if item and "decision" in item:
                    decision = item["decision"]
                    if "next" in decision:
                        return repeated_dispatch(args, state["assignments"][decision["next"]])
                    if args.brief:
                        raise HandoffError("this receipt was consumed without a next assignment")
                    return decision, 0
                if not item or key != state["current"]:
                    raise HandoffError("wrong or stale assignment")
                if "receipt" not in item:
                    raise HandoffError("no saved receipt to consume")
                phase, following = next_step(state, item)
                if following is None:
                    if args.brief:
                        raise HandoffError(f"this receipt ends dispatch ({phase}); omit --brief")
                    item["decision"] = {"phase": phase}
                    state.update(phase=phase, current=None)
                    run.save(state)
                    return item["decision"], 0
                if not args.brief:
                    raise HandoffError("supply --brief for the next assignment")
                brief = text_file(args.brief)
                item["decision"] = {"phase": phase, "next": assignment_key(*following)}
                state["phase"] = phase
                item = new_assignment(state, *following, brief)
            message = start_intent(run, state, item)
        return finish_send(run, item, message, dispatch_fd)


def repeated_dispatch(args, item):
    if args.brief and text_file(args.brief) != item["brief"]:
        raise HandoffError("next assignment already exists with a different brief")
    return dispatch_summary(item)


def reconcile(run, args):
    if not args.evidence.strip():
        raise HandoffError("inspection evidence is required")
    with run.lock("dispatch", nonblocking=True) as dispatch_fd:
        with run.lock():
            state = run.load()
            require_leader(state)
            key = assignment_key(args.role, args.round)
            if key != state["current"]:
                raise HandoffError("wrong or stale assignment")
            item = state["assignments"][key]
            attempt = item["attempts"][-1]
            if ("receipt" in item or args.attempt != len(item["attempts"])
                    or attempt["status"] not in ("intent", "ambiguous")):
                raise HandoffError("that send attempt no longer needs reconciliation")
            attempt["inspection"] = {"decision": args.decision, "evidence": args.evidence}
            if args.decision == "delivered":
                attempt["status"] = "delivered-by-inspection"
                run.save(state)
                return dispatch_summary(item)
            attempt["status"] = "not-delivered-by-inspection"
            message = start_intent(run, state, item)
        return finish_send(run, item, message, dispatch_fd)


def report(run, args):
    with run.lock():
        state = run.load()
        key = assignment_key(args.role, args.round)
        if key != state["current"]:
            raise HandoffError("wrong or stale assignment")
        item = state["assignments"][key]
        if identity() != item["target"]:
            raise HandoffError("this command requires the assigned worker terminal")
        if args.status not in ROLES[args.role]:
            raise HandoffError(f"invalid {args.role} status; use {', '.join(ROLES[args.role])}")
        receipt = {"status": args.status, "result": text_file(args.result), "worker": item["target"]}
        if "receipt" in item:
            if item["receipt"] != receipt:
                raise HandoffError("conflicting receipt; original was kept")
        else:
            item["receipt"] = receipt
            run.save(state)
        leader = state["leader"]
    message = (f"Herdr Dev Team receipt saved for {item['id']} ({args.status}). "
               f"Run directory: {run.path}. Use resume to read it before advancing.")
    outcome = send(leader, message)
    return {"assignment": item["id"], "receipt_saved": True, "target": leader,
            "notification": outcome}, int(outcome["status"] != "delivered")


def takeover_guidance(state):
    return {
        "receipt_route": f"Explicit worker receipt notifications target {state['leader']}.",
        "core_note_route": "Automatic Core blocked, error, and exit notes still target each worker's original parent.",
        "inspect_once": {role: f"hc get --json {target}" for role, target in state["workers"].items()},
        "action": "Run each inspection command once and compare with the saved receipts. Ask the operator to "
                  "alert the current Leader if an inherited worker later blocks, errors, or exits without a receipt. "
                  "On that alert, use resume and inspect that worker once.",
    }


def takeover(run, args):
    with run.lock("dispatch", nonblocking=True), run.lock():
        state = run.load()
        leader = identity()
        if args.previous != state["leader"]:
            raise HandoffError("--from does not match the recorded Leader terminal")
        if leader in state["workers"].values():
            raise HandoffError("a worker terminal cannot take over as Leader")
        if leader != state["leader"]:
            state.setdefault("former_leaders", []).append(state["leader"])
            state["leader"] = leader
            run.save(state)
        result = {"leader": leader}
        if state.get("former_leaders"):
            result["takeover_guidance"] = takeover_guidance(state)
        return result, 0


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run_dir", type=Path, help="private shared local run directory")
    commands = cli.add_subparsers(dest="command", required=True)
    for pack in ("two", "four"):
        command = commands.add_parser(pack, add_help=False, help=f"manage a SQLite {pack}-pack run")
        command.add_argument("arguments", nargs=argparse.REMAINDER, metavar="init | RUN_ID COMMAND")
    init = commands.add_parser("init", help="record the user-confirmed settings and terminal IDs")
    init.add_argument("--confirm", action="store_true", help="affirm that the user confirmed the settings")
    init.add_argument("--config", required=True, type=Path, help="UTF-8 file with task and effective settings")
    init.add_argument("--project", required=True, type=Path)
    for role in ROLES:
        init.add_argument(f"--{role}", required=True, type=terminal)
    init.add_argument("--rounds", type=positive, default=5, help="confirmed review budget (default: 5)")
    commands.add_parser("resume", help="read saved state and receipts without sending anything")
    adopt = commands.add_parser("takeover", help="move Leader authority to the current terminal")
    adopt.add_argument("--from", dest="previous", required=True, type=terminal)
    start = commands.add_parser("start", help="dispatch Coder round 1, once")
    start.add_argument("--brief", required=True, type=Path)
    for name, help_text in (
        ("report", "save a worker receipt, then notify the Leader"),
        ("advance", "consume a verified receipt and dispatch the next assignment, if any"),
        ("reconcile", "record inspection evidence for an ambiguous dispatch"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("role", choices=ROLES)
        command.add_argument("round", type=positive)
        if name == "report":
            command.add_argument("status", choices=sorted({status for statuses in ROLES.values() for status in statuses}))
            command.add_argument("--result", required=True, type=Path, help="UTF-8 evidence file")
        elif name == "advance":
            command.add_argument("--brief", type=Path, help="UTF-8 brief for the next worker")
        else:
            command.add_argument("attempt", type=positive, help="ambiguous send attempt number")
            command.add_argument("decision", choices=("delivered", "retry"),
                                 help="retry only if inspection proves the original send cannot arrive")
            command.add_argument("--evidence", required=True, help="what inspection established")
    return cli


def main():
    # Let the pack parser own help and options below its namespace.
    if len(sys.argv) >= 3 and sys.argv[2] in ("two", "four"):
        from pack_evidence import validate
        import two_pack

        try:
            result, code = two_pack.main(Path(sys.argv[1]), sys.argv[3:], identity=identity,
                                        send=send, validate=validate, pack=sys.argv[2])
        except (HandoffError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"handoff: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    args = parser().parse_args()
    run = Run(args.run_dir)
    try:
        if args.command == "resume":
            with run.lock():
                result, code = run.load(), 0
            if result.get("former_leaders"):
                result["takeover_guidance"] = takeover_guidance(result)
        else:
            commands = {"init": initialize, "start": advance, "advance": advance,
                        "reconcile": reconcile, "report": report, "takeover": takeover}
            result, code = commands[args.command](run, args)
    except (HandoffError, OSError, ValueError) as exc:
        print(f"handoff: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
