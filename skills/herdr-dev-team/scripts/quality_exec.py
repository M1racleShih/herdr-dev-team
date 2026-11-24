"""Run frozen quality checks and archive the actual command evidence."""

from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path, PurePath
import signal
import stat
import subprocess
import sys
import tempfile

import quality_store
from pack_evidence import archived_path, required_text, worktree
from pack_store import (
    assignment_id, assignment_key, encode, get_assignment, get_attempt, get_run, guard_message,
    private, project_baseline, touch_run, transaction,
)
from spec_artifact import checked_out_spec


def check_project(db, run, item, spec, commit):
    from four_pack import changed_examples

    project = Path(run["project"])
    if project_baseline(project) != (run["project_id"], run["branch"], commit):
        raise ValueError("the check must run in the recorded Git project, branch and candidate commit")
    worktree(project, run["branch"], item["input_commit"], commit, require_clean=True, allow_same_tree=True)
    if changed_examples(db, run) or checked_out_spec(project, commit, spec["path"])["sha256"] != spec["sha256"]:
        raise ValueError("the check must use the exact approved examples")


def resolve_check(db, item, plan, name, temporary):
    role = item["role"]
    definitions = {check["id"]: check for check in plan["plan"]["checks"]}
    if temporary:
        if temporary["owner"] != role or temporary["use"] != "observe":
            raise ValueError("a temporary check must be an observation owned by the assigned role")
        if temporary["id"] in definitions:
            raise ValueError(f"temporary check {temporary['id']} conflicts with a planned check")
        prior = quality_store.temporary_definition(db, item, temporary["id"])
        if prior and prior["check_definition"] != encode(temporary):
            raise ValueError(f"temporary check {temporary['id']} conflicts with its frozen definition")
        return temporary
    check = definitions.get(name)
    if check is None:
        raise ValueError(f"unknown planned check {name}")
    if check["owner"] != role and not (role == "qa" and check["use"] == "final"):
        raise ValueError(f"{role} cannot execute check {name} owned by {check['owner']}")
    return check


def process_status(store, row):
    path = store / row["directory"] / "process.lock"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as lock:
            info = os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != (row["lock_device"], row["lock_inode"])):
                return "unknown"
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "running"
    except OSError:
        return "unknown"
    return "interrupted"


def allocate_check(stack, store):
    parent = store / "quality-checks"
    parent.mkdir(mode=0o700, exist_ok=True)
    private(parent)
    directory = Path(tempfile.mkdtemp(prefix="check-", dir=parent))
    (directory / "artifacts").mkdir(mode=0o700)
    lock = stack.enter_context((directory / "process.lock").open("xb"))
    fcntl.flock(lock, fcntl.LOCK_EX)
    return directory, lock.fileno()


def snapshot(store, data):
    path = archived_path(store, data)
    return {"path": path, "sha256": PurePath(path).name}


def archive_logs(store, directory, evidence):
    issues = []
    for name in ("stdout", "stderr"):
        try:
            data, _ = local_artifact(directory, name)
            evidence[name] = snapshot(store, data)
        except (OSError, ValueError, RuntimeError) as exc:
            issues.append(f"cannot archive {name}: {exc}")
    return issues


def recover_interrupted(db, store, run):
    for row in list(quality_store.executions(db, run["id"], running=True)):
        if process_status(store, row) != "interrupted":
            continue
        evidence = {}
        issues = archive_logs(store, store / row["directory"], evidence)
        summary = "; ".join(["execution was interrupted before its evidence was finalized", *issues])
        argv = json.loads(row["check_definition"])["argv"]
        quality_store.finish_execution(db, row["id"], outcome="inconclusive", summary=summary,
                                      return_code=None, tool=argv[0], configuration=encode(argv), evidence=evidence)
        touch_run(db, run)


def execute(directory, project, spec, commit, definition, lock_fd):
    # The supervisor retains the inherited lock even if the adapter closes its inherited descriptors.
    # A separate status pipe keeps the adapter's exit code independent of its output files.
    artifacts = directory / "artifacts"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HERDR_CHECK_COMMIT=commit, HERDR_APPROVED_SPEC=str(project / spec["path"]),
               HERDR_CHECK_DIR=str(artifacts), HERDR_CHECK_RESULT=str(artifacts / "result.json"))
    read_fd, write_fd = os.pipe()
    try:
        with (directory / "stdout").open("xb") as stdout, (directory / "stderr").open("xb") as stderr:
            command = [sys.executable, "-B", str(Path(__file__).resolve()), str(write_fd), str(lock_fd),
                       str(definition.get("timeout_seconds", 0)), *definition["argv"]]
            supervisor = subprocess.Popen(command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                          stdout=stdout, stderr=stderr, pass_fds=(write_fd, lock_fd),
                                          start_new_session=True)
    except OSError as exc:
        os.close(read_fd)
        return None, f"could not start check supervisor: {exc}"
    finally:
        os.close(write_fd)
    with os.fdopen(read_fd, "rb") as status:
        # Reap the supervisor even if its status pipe was closed without a response.
        response = status.read()
        supervisor.wait()
    if supervisor.returncode:
        return None
    try:
        result = json.loads(response)
    except ValueError:
        return None
    return result["return_code"], result["error"]


def supervise(status_fd, lock_fd, timeout, argv):
    return_code, error = None, None
    try:
        process = subprocess.Popen(argv, pass_fds=(lock_fd,), start_new_session=True)
    except OSError as exc:
        error = f"could not start check command: {exc}"
    else:
        try:
            return_code = process.wait(timeout=min(timeout, sys.maxsize) if timeout else None)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return_code = process.wait()
            error = f"check command timed out after {timeout} seconds"
    try:
        with os.fdopen(status_fd, "w") as status:
            json.dump({"return_code": return_code, "error": error}, status)
    except BrokenPipeError:
        # The executor was interrupted. Releasing the lock leaves the original intent recoverable.
        pass


def local_artifact(directory, name):
    path = (directory / name).resolve(strict=True)
    if directory not in path.parents:
        raise ValueError(f"evidence file must be inside HERDR_CHECK_DIR: {name}")
    with ExitStack() as stack:
        directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
        parent = os.open(directory, directory_flags)
        stack.callback(os.close, parent)
        for part in path.relative_to(directory).parts[:-1]:
            parent = os.open(part, directory_flags, dir_fd=parent)
            stack.callback(os.close, parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"evidence file must be a regular file: {name}")
            return stream.read(), (info.st_dev, info.st_ino)


def adapter_result(store, directory, evidence):
    artifacts = directory / "artifacts"
    data, result_file = local_artifact(artifacts, "result.json")
    evidence["result"] = snapshot(store, data)
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("outcome") not in ("pass", "fail", "inconclusive"):
        raise ValueError("result file must name a pass, fail or inconclusive outcome")
    for field in ("summary", "tool", "configuration"):
        guard_message(required_text(result, field, "check result"))
    names = result.get("artifacts")
    if (not isinstance(names, list) or not all(quality_store.valid_text(name) for name in names)
            or (not names and result["outcome"] != "inconclusive")):
        raise ValueError("result file requires an artifacts list with raw evidence for a pass or fail")
    evidence["artifacts"] = []
    for name in names:
        guard_message(name)
        data, artifact_file = local_artifact(artifacts, name)
        if artifact_file == result_file:
            raise ValueError("the adapter result file cannot serve as its own raw evidence")
        evidence["artifacts"].append({"source": name, **snapshot(store, data)})
    return {field: result[field] for field in ("outcome", "summary", "tool", "configuration")}


def conclude(store, directory, definition, return_code, error):
    evidence = {}
    details = {"tool": definition["argv"][0], "configuration": encode(definition["argv"])}
    issues = archive_logs(store, directory, evidence)
    if error:
        issues.insert(0, error)
    if not error and return_code is not None and return_code < 0:
        issues.insert(0, f"check command ended on signal {-return_code}")
    if issues:
        details.update(outcome="inconclusive", summary="; ".join(issues))
    elif definition["protocol"] == "result" and return_code == 0:
        try:
            details.update(adapter_result(store, directory, evidence))
        except (OSError, ValueError, RuntimeError) as exc:
            details.update(outcome="inconclusive", summary=f"check result could not be verified: {exc}")
    else:
        outcome = "inconclusive"
        if definition["protocol"] == "exit":
            if return_code == 0:
                outcome = "pass"
            elif return_code in definition["fail_codes"]:
                outcome = "fail"
        details.update(outcome=outcome, summary=f"check command exited with status {return_code}")
    return {**details, "return_code": return_code, "evidence": evidence}


def result_view(row):
    return {"execution_id": row["id"], "check": row["check_id"],
            "plan_version": row["plan_version"], "outcome": row["outcome"]}


def check(db, store, run, role, round_number, check_id, key, identity, *, observe=None):
    from four_pack import latest_spec

    if not quality_store.valid_text(key):
        raise ValueError("check requires a nonempty --key")
    guard_message(key)
    if (check_id is None) == (observe is None):
        raise ValueError("name a planned check ID or provide --observe FILE")
    temporary = quality_store.read_check(observe) if observe is not None else None
    source = guard_message(str(Path(observe).resolve())) if observe is not None else None
    worker = identity()
    with ExitStack() as stack:
        with transaction(db):
            run = get_run(db, run["id"], "six")
            item = get_assignment(db, assignment_id(run, role, round_number))
            if item is None:
                raise ValueError("wrong or stale assignment")
            if worker != item["target"]:
                raise ValueError("this command requires the assigned worker terminal")
            if get_attempt(db, item["id"]) is None:
                raise ValueError("assignment has not been dispatched")
            plan = quality_store.assignment_plan(db, item)
            if plan is None:
                raise ValueError("check requires a six-pack run initialized with --plan")
            spec = latest_spec(db, run, round_number)
            if not spec or not spec["approval"] or role not in quality_store.OWNERS:
                raise ValueError("check requires an approved specification and an engineering assignment")
            current = run["phase"] == "active" and run["current"] == assignment_key(role, round_number)
            if current:
                recover_interrupted(db, store, run)
            prior = quality_store.execution_for_key(db, item, key)
            if not current and not prior:
                raise ValueError("wrong or stale assignment")
            definition = resolve_check(db, item, plan, check_id, temporary)
            project = Path(run["project"])
            project_id, branch, commit = project_baseline(project)
            if (project_id, branch) != (run["project_id"], run["branch"]):
                raise ValueError("the worktree must be at the recorded project and branch")
            if prior:
                if ((prior["plan_version"], prior["spec_version"], prior["spec_sha256"], prior["commit"],
                     prior["check_definition"], prior["source"] is not None)
                        != (plan["plan_version"], spec["version"], spec["sha256"], commit,
                            encode(definition), temporary is not None)):
                    raise ValueError(f"check key {key} conflicts with the original execution {prior['id']}")
                return result_view(prior)
            check_project(db, run, item, spec, commit)
            directory, lock_fd = allocate_check(stack, store)
            execution_id = quality_store.register_execution(
                db, run, item, plan, spec, definition, commit, key, source, str(directory.relative_to(store)),
                os.fstat(lock_fd),
            )
            touch_run(db, run)

        result = execute(directory, project, spec, commit, definition, lock_fd)
        if result is None:
            # The supervisor disappeared before confirming that the command exited.
            return result_view(quality_store.get_execution(db, execution_id))
        return_code, error = result
        details = conclude(store, directory, definition, return_code, error)
        try:
            check_project(db, run, item, spec, commit)
        except (OSError, ValueError) as exc:
            details.update(outcome="inconclusive", summary=f"check changed the worktree or specification: {exc}")
        with transaction(db):
            quality_store.finish_execution(db, execution_id, **details)
            touch_run(db, run)
            return result_view(quality_store.get_execution(db, execution_id))


def resume(db, store, run, view):
    plan = quality_store.get_plan(db, run["id"])
    if plan is None:
        return
    plans = quality_store.plan_history(db, run["id"])
    history = {version["plan_version"]: version["plan"] for version in plans}
    seen = []
    by_assignment = {}
    by_id = {}
    for row in quality_store.executions(db, run["id"]):
        execution = quality_store.execution_view(row)
        if execution["outcome"] == "running":
            execution["status"] = process_status(store, row)
        seen.append(execution)
        by_assignment.setdefault(execution["assignment_id"], []).append(execution)
        by_id[execution["id"]] = execution

    final = quality_store.final_evidence(db, run["id"])
    for item in view["assignments"].values():
        assigned = quality_store.assignment_plan(db, item)
        if assigned is None:
            continue
        version, role = assigned["plan_version"], item["role"]
        checks = {definition["id"]: {"definition": definition, "outcome": "untested"}
                  for definition in history[version]["checks"]
                  if definition["owner"] == role or (role == "qa" and definition["use"] == "final")}
        results = {entry["check_id"]: entry for entry in by_assignment.get(item["id"], [])}
        if final and final["receipt_id"] == item.get("receipt", {}).get("id"):
            results.update({name: by_id[execution_id] for name, execution_id in final["executions"].items()})
        for name, execution in results.items():
            checks[name] = {
                "definition": execution["check_definition"], "execution_id": execution["id"],
                "outcome": execution["outcome"], "status": execution["status"], "summary": execution["summary"],
                "assignment_id": execution["assignment_id"],
            }
        item.update(plan_version=version, quality_checks=checks)
    view["quality"] = {
        "plan_version": plan["plan_version"], "plan": plan["plan"], "plan_versions": plans,
        "executions": seen, "final": final,
    }


if __name__ == "__main__":
    write_fd, lock_fd, timeout, *argv = sys.argv[1:]
    supervise(int(write_fd), int(lock_fd), int(timeout), argv)
