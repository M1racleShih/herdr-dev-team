#!/usr/bin/env python3
"""Dispatch and recover Herdr Dev Team packs in a shared local SQLite store."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from pack_evidence import validate
import two_pack


def terminal(value):
    if not re.fullmatch(r"t[0-9a-z]+", value):
        raise ValueError("use a literal local Herdr terminal ID (for example, t7)")
    return value


def identity():
    return terminal(os.environ.get("HC_TERMINAL_ID", ""))


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


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("store", type=Path, metavar="STORE", help="private project store outside the worktree")
    cli.add_argument("pack", choices=("two", "four", "six"), help="pack size")
    cli.add_argument("arguments", nargs=argparse.REMAINDER, metavar="ARGS", help="init or RUN_ID COMMAND")
    args = cli.parse_args()
    try:
        result, code = two_pack.main(args.store, args.arguments, identity=identity,
                                    send=send, validate=validate, pack=args.pack)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"handoff: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
