"""Match six-pack handoffs and final QA evidence to executed plan checks."""

import json

from pack_evidence import required_text
from pack_store import accepted_receipt
from quality_store import execution_view, get_execution, latest_execution


REQUIRED_USES = {"stage", "final"}


def accepted_owner_pass(db, execution):
    if execution["outcome"] != "pass" or execution["role"] != execution["owner"]:
        return False
    receipt = accepted_receipt(db, execution["assignment_id"])
    if receipt is None:
        return False
    quality = json.loads(receipt["payload"])["quality"]
    return execution["id"] in quality["executions"]


def referenced_executions(db, run, item, plan, spec, payload):
    if item["role"] == "specifier" and "quality" not in payload:
        return {}
    quality = payload.get("quality")
    if (not isinstance(quality, dict) or quality.keys() != {"plan_version", "executions"}
            or type(quality["plan_version"]) is not int or quality["plan_version"] != plan["plan_version"]):
        raise ValueError("receipt quality must name the assignment's plan_version and executions")
    ids = quality["executions"]
    if (not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("receipt quality executions must be a list of distinct execution IDs")
    executions = {}
    for execution_id in ids:
        execution = get_execution(db, execution_id)
        if (execution is None or spec is None
                or (execution["run_id"], execution["plan_version"], execution["spec_version"],
                    execution["spec_sha256"], execution["commit"])
                != (run["id"], plan["plan_version"], spec["version"], spec["sha256"], payload.get("commit"))):
            raise ValueError(f"execution {execution_id} must match the run, plan, approved spec and receipt commit")
        if execution["assignment_id"] != item["id"] and not (
                item["role"] == "qa" and execution["use"] == "final" and accepted_owner_pass(db, execution)):
            raise ValueError(f"execution {execution_id} has the wrong source assignment")
        if execution["outcome"] == "running":
            raise ValueError(f"execution {execution_id} is still running")
        executions[execution_id] = execution
    return executions


def latest_matching(db, run, plan, spec, check_id, commit):
    return latest_execution(db, run["id"], plan["plan_version"], spec["version"],
                            spec["sha256"], check_id, commit)


def require_pass(check_id, execution):
    if execution is None:
        raise ValueError(f"missing required execution for check {check_id}")
    if execution["outcome"] != "pass":
        raise ValueError(f"check {check_id} execution {execution['id']} is {execution['outcome']}: "
                         f"{execution['summary']}")


def validate_quality(db, run, item, plan, spec, status, payload):
    """Return the validated project failure ID or the final check-to-execution index."""
    if not isinstance(payload, dict):
        raise ValueError("receipt must be an object")
    executions = referenced_executions(db, run, item, plan, spec, payload)
    role = item["role"]
    if role == "specifier":
        return None, None
    if status == "fail":
        failure = payload.get("failure")
        if not isinstance(failure, dict):
            raise ValueError("fail receipt requires failure details")
        check = required_text(failure, "check", "failure")
        if role == "qa" and check == "user-entry":
            return None, None
        failed = failed_execution(executions.values(), check)
        if failed is None:
            raise ValueError(f"failure must reference a failed stage or final execution for check {check}")
        latest = latest_matching(db, run, plan, spec, check, payload.get("commit"))
        if latest["id"] != failed["id"]:
            raise ValueError(f"failure must reference the latest execution for check {check}. "
                             f"Execution {latest['id']} is {latest['outcome']}")
        return check, None
    if status not in {"done", "pass"}:
        return None, None

    finals = {}
    for check in plan["plan"]["checks"]:
        check_id, owner, use = check["id"], check["owner"], check["use"]
        owned_required = owner == role and use in REQUIRED_USES
        completing_final = role == "qa" and status == "pass" and use == "final"
        if not (owned_required or completing_final):
            continue
        latest = latest_matching(db, run, plan, spec, check_id, payload.get("commit"))
        require_pass(check_id, latest)
        if latest["assignment_id"] == item["id"]:
            if latest["id"] not in executions:
                raise ValueError(f"check {check_id} must reference the latest execution {latest['id']}")
        elif owned_required:
            raise ValueError(f"check {check_id} needs a passing execution from this assignment")
        elif not accepted_owner_pass(db, latest):
            raise ValueError(f"check {check_id} needs a passing execution from this assignment "
                             "or an accepted receipt from its owner")
        if use == "final":
            finals[check_id] = latest["id"]
    for execution in executions.values():
        if (execution["use"] == "final" and execution["outcome"] == "pass"
                and execution["id"] != finals[execution["check_id"]]):
            raise ValueError(f"check {execution['check_id']} pass {execution['id']} is superseded by "
                             f"{finals[execution['check_id']]}")
    return None, finals if role == "qa" and status == "pass" else None


def failed_execution(executions, check):
    return max((execution for execution in executions
                if execution["check_id"] == check and execution["use"] in REQUIRED_USES
                and execution["outcome"] == "fail"), key=lambda execution: execution["sequence"], default=None)


def quality_finding_context(db, payload):
    executions = (get_execution(db, execution_id) for execution_id in payload["quality"]["executions"])
    execution = failed_execution(executions, payload["failure"]["check"])
    evidence = execution_view(execution)
    return (f"Failed plan check {execution['check_id']} at {execution['commit']} "
            f"(plan {execution['plan_version']}, execution {execution['id']}).\n"
            f"{json.dumps(evidence, ensure_ascii=True, indent=2)}\n")
