"""Approved user-entry expectations and independent QA evidence for the six-pack."""

import hashlib
import json
from pathlib import Path
import shlex


SIX_STATUSES = {
    "specifier": {"done", "blocked", "paused"},
    "coder": {"done", "blocked", "paused", "superseded"},
    "cleaner": {"done", "fail", "blocked", "paused", "superseded"},
    "architect": {"done", "fail", "blocked", "paused", "superseded"},
    "hardener": {"done", "fail", "blocked", "paused", "superseded"},
    "qa": {"pass", "fail", "blocked", "paused", "superseded"},
}
SIX_CHECKS = {
    "specifier": {}, "coder": {"unit": 0, "acceptance": 0},
    "cleaner": {"unit": 0, "acceptance": 0},
    "architect": {"unit": 0, "acceptance": 0, "properties": 0},
    "hardener": {"unit": 0, "acceptance": 0, "mutation": 1},
    "qa": {"user-entry": 0},
}


def json_object(body, description):
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        value = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{description} must be a UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a UTF-8 JSON object")
    return value


def observation_rows(rows, description):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{description} must be a nonempty list")
    for row in rows:
        if (not isinstance(row, dict) or row.keys() != {"command", "stdout", "stderr", "exit_code"}
                or not isinstance(row["command"], str) or not row["command"].strip()
                or not isinstance(row["stdout"], str) or not isinstance(row["stderr"], str)
                or type(row["exit_code"]) is not int or not -255 <= row["exit_code"] <= 255):
            raise ValueError(f"{description} rows require command, stdout, stderr and integer exit_code")
    return rows


def validate_six_spec(body):
    """Return the parsed specification, preserving each expected observation verbatim."""
    spec = json_object(body, "six-pack specification")
    if not isinstance(spec.get("examples"), list) or not spec["examples"]:
        raise ValueError("six-pack specification requires a nonempty examples list")
    observation_rows(spec.get("qa_steps"), "six-pack specification qa_steps")
    return spec


def validate_qa(project, script_path, script, log, report, status, observations, expected):
    """Bind the independent harness and observed user-entry output to the receipt."""
    try:
        args = shlex.split(report["command"])
        names_script = any((project / Path(arg)).resolve() == script_path for arg in args)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("QA user-entry command must name the private script") from exc
    if not names_script:
        raise ValueError("QA user-entry command must name the private script")

    observations = observation_rows(observations, "QA observations")
    if [row["command"] for row in observations] != [row["command"] for row in expected]:
        raise ValueError("QA observations must report each approved command in order")
    result = json_object(log, "QA user-entry log")
    observation_rows(result.get("observations"), "QA log observations")
    if (type(result.get("exit_code")) is not int or result != {
            "commit": report["commit"], "script_sha256": hashlib.sha256(script).hexdigest(),
            "observations": observations, "exit_code": report["exit_code"],
    }):
        raise ValueError("QA user-entry log must match the commit, script, observations and exit code")
    if observations != expected and status == "pass":
        raise ValueError("QA pass requires observations matching the approved qa_steps")
    if observations == expected and status == "fail":
        raise ValueError("QA fail requires a mismatch with the approved qa_steps")
