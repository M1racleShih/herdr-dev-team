"""Validate engineering pack receipts against the assigned worktree and pinned checks."""

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


STATUSES = {"coder": {"done", "blocked", "paused"}, "cleaner": {"pass", "fail", "blocked", "paused"}}
REQUIRED_CHECKS = {"coder": {"unit": 0}, "cleaner": {"unit": 0, "mutation": 1}}
FOUR_STATUSES = {
    "specifier": {"done", "blocked", "paused"},
    "coder": {"done", "blocked", "paused", "superseded"},
    "refactorer": {"done", "fail", "blocked", "paused", "superseded"},
    "architect": {"pass", "fail", "blocked", "paused", "superseded"},
}
FOUR_CHECKS = {
    "specifier": {}, "coder": {"unit": 0, "acceptance": 0},
    "refactorer": {"unit": 0, "acceptance": 0, "properties": 0},
    "architect": {"unit": 0, "acceptance": 0, "dependencies": 0, "mutation": 1, "example-mutation": 1},
}
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def required_text(record, key, description):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description} requires nonempty {key}")
    return value


def validate_checks(checks, *, pack="two"):
    """Return pinned checks by role and name after validating the task configuration."""
    required_checks = FOUR_CHECKS if pack == "four" else REQUIRED_CHECKS
    if not isinstance(checks, dict) or checks.keys() != required_checks.keys():
        names = ", ".join(required_checks) if pack == "four" else "coder and cleaner"
        raise ValueError(f"checks must define {names}")
    by_role = {}
    for role, required in required_checks.items():
        if not isinstance(checks[role], list):
            raise ValueError(f"{role} checks must be a list")
        by_name = {}
        for check in checks[role]:
            if not isinstance(check, dict):
                raise ValueError(f"{role} checks must contain objects")
            name = required_text(check, "name", f"{role} check")
            required_text(check, "command", f"{role} check {name}")
            status = check.get("exit_code")
            if type(status) is not int or not 0 <= status <= 255:
                raise ValueError(f"{role} check {name} requires an integer exit_code from 0 to 255")
            if name in by_name:
                raise ValueError(f"duplicate {role} check {name}")
            by_name[name] = check
        for name, status in required.items():
            if name not in by_name or by_name[name]["exit_code"] != status:
                raise ValueError(f"{role} requires check {name} with exit code {status}")
        by_role[role] = by_name
    final_role = "architect" if pack == "four" else "cleaner"
    final = by_role[final_role]
    mutations = [("mutation", "unit")]
    if pack == "four":
        mutations.append(("example-mutation", "acceptance"))
        acceptance = final["acceptance"]["command"]
        if any(by_role[role]["acceptance"]["command"] != acceptance for role in ("coder", "refactorer")):
            raise ValueError("all downstream roles must use the same acceptance command")
    for name, baseline in mutations:
        mutation = final[name]
        if mutation["command"] != final[baseline]["command"]:
            raise ValueError(f"{final_role} {baseline} and {name} checks must use the same command")
        if "test" in mutation or "assertion" in mutation:
            required_text(mutation, "test", "pinned mutation check")
            required_text(mutation, "assertion", "pinned mutation check")
    return by_role


def git(project, *args, allowed=(0,), env=None, data=None):
    # The calling process may itself be working with a different Git index or repository.
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    git_env.update(env or {})
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", os.fspath(project), *args],
            env=git_env, input=data, capture_output=True, check=False,
        )
    except OSError as exc:
        raise ValueError(f"could not run Git: {exc}") from exc
    if result.returncode not in allowed:
        detail = result.stderr.decode(errors="replace").strip()
        raise ValueError(f"git {args[0]} failed: {detail or result.returncode}")
    return result


def worktree(project, branch, input_commit, commit, *, require_clean, allow_same_tree=False):
    root = Path(os.fsdecode(git(project, "rev-parse", "--show-toplevel").stdout).rstrip("\n"))
    if root != project:
        raise ValueError("project must be the root of the assigned Git worktree")
    actual_branch = git(project, "symbolic-ref", "-q", "HEAD", allowed=(0, 1)).stdout.rstrip(b"\n")
    if actual_branch != os.fsencode(f"refs/heads/{branch}"):
        raise ValueError(f"assigned worktree must be on branch {branch}")
    head = git(project, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    if head != commit:
        raise ValueError(f"receipt commit must match assigned worktree HEAD ({head})")
    ancestor = git(project, "merge-base", "--is-ancestor", input_commit, commit, allowed=(0, 1))
    if ancestor.returncode:
        raise ValueError("receipt commit must descend from the assigned input commit")
    if require_clean:
        dirty = git(project, "status", "--porcelain=v1", "--untracked-files=all",
                    "--ignore-submodules=none").stdout
        if dirty:
            raise ValueError("assigned worktree has uncommitted changes")
        if input_commit != commit and not allow_same_tree:
            input_tree, output_tree = git(
                project, "rev-parse", f"{input_commit}^{{tree}}", f"{commit}^{{tree}}",
            ).stdout.splitlines()
            if input_tree == output_tree:
                raise ValueError("check-only receipts must use the assigned input commit")


def evidence_bytes(store, project, record, key, description):
    value = required_text(record, key, description)
    try:
        path = (store / value).resolve(strict=True)
        if store not in path.parents or project in path.parents:
            raise ValueError(f"{description} {key} must be in the private store outside the worktree")
        if not path.is_file():
            raise ValueError(f"{description} {key} must be a regular file")
        data = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot read {description} {key}: {exc}") from exc
    if not data.strip():
        raise ValueError(f"{description} {key} is empty")
    return data


def archived_path(store, data):
    archive = store / "evidence"
    name = hashlib.sha256(data).hexdigest()
    destination = archive / name
    try:
        archive.mkdir(mode=0o700, exist_ok=True)
        if archive.is_symlink():
            raise ValueError("evidence archive directory must be inside the private store")
        if not destination.exists():
            with tempfile.NamedTemporaryFile(dir=archive, prefix=".evidence-") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                try:
                    os.link(stream.name, destination)
                except FileExistsError:
                    pass
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != data:
            raise ValueError("evidence archive contains a corrupt snapshot")
        for directory in (archive, store):
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError as exc:
        raise ValueError(f"cannot save evidence snapshot: {exc}") from exc
    return str(destination.relative_to(store))


def replay_patch(project, commit, patch, *, example_path=None):
    """Apply the patch to a temporary index and object directory for this exact commit."""
    object_path = git(project, "rev-parse", "--git-path", "objects").stdout.rstrip(b"\n")
    common_objects = project / Path(os.fsdecode(object_path))
    try:
        with tempfile.TemporaryDirectory(prefix="herdr-mutation-") as directory:
            scratch = Path(directory)
            objects = scratch / "objects"
            (objects / "info").mkdir(parents=True)
            (objects / "info" / "alternates").write_bytes(os.fsencode(common_objects) + b"\n")
            env = {"GIT_INDEX_FILE": str(scratch / "index"), "GIT_OBJECT_DIRECTORY": str(objects)}
            git(project, "read-tree", commit, env=env)
            git(project, "apply", "--cached", "--whitespace=nowarn", "-", env=env, data=patch)
            args = ("diff", "--cached", "--no-renames", "--no-ext-diff", "-z")
            changed = git(project, *args, "--name-only", commit, env=env).stdout
            if example_path is not None:
                lines = git(project, *args, "--numstat", commit, env=env).stdout
                modes = git(project, *args, "--summary", commit, env=env).stdout
    except (OSError, ValueError) as exc:
        raise ValueError(f"mutation patch cannot be replayed against receipt commit: {exc}") from exc
    if not changed:
        raise ValueError("mutation patch must change the receipt commit")
    names = {os.fsdecode(name) for name in changed.split(b"\0") if name}
    if example_path is not None and (names != {example_path} or modes
                                    or lines != b"1\t1\t" + os.fsencode(example_path) + b"\0"):
        raise ValueError("example mutation must replace one line of only the approved specification file")
    return names


def mutation_proof(project, commit, mutation, pinned, green, red, patch, *, example_path=None):
    test = required_text(mutation, "test", "mutation check")
    assertion = required_text(mutation, "assertion", "mutation check")
    for key, value in (("test", test), ("assertion", assertion)):
        if key in pinned and pinned[key] != value:
            raise ValueError(f"mutation {key} must match pinned check")
    if not re.fullmatch(r"[\w.]+\.[\w]+", test) or ".." in test or test.startswith("."):
        raise ValueError("mutation test must be a qualified unittest test ID")
    method = test.rsplit(".", 1)[1]
    # unittest can display a qualified ID with or without the repeated method name.
    test_names = "|".join(map(re.escape, (test, test.rsplit(".", 1)[0])))
    display = rf"{re.escape(method)} \((?:{test_names})\)"
    success = green.decode("utf-8", errors="replace")
    failure = red.decode("utf-8", errors="replace")
    if not re.search(rf"^{display}(?:\r?\n[^\r\n]+)? \.\.\. ok[ \r]*$", success, re.M):
        raise ValueError(f"unit log must show mutation test {test} passed on the original commit")
    matching_failure = re.search(
        rf"^FAIL: {display}[ \r]*\n(.*?)(?=^={{10,}}[ \r]*$|\Z)", failure, re.M | re.S,
    )
    if not matching_failure:
        raise ValueError(f"mutation log must show {test} failed with the declared assertion")
    trace = matching_failure[1]
    assertion_line = re.search(r"^AssertionError(?::[ \t]*|[ \r]*$)", trace, re.M)
    if not assertion_line or assertion not in trace[assertion_line.end():].split("\n\n", 1)[0]:
        raise ValueError(f"mutation log must show {test} failed with the declared assertion")
    return replay_patch(project, commit, patch, example_path=example_path)


def validate(project: Path, branch: str, input_commit: str, role: str, status: str,
             payload: dict, store: Path, checks: dict, *, pack="two", spec_path=None) -> None:
    """Validate a receipt and add durable snapshot locations to its evidence records."""
    statuses = FOUR_STATUSES if pack == "four" else STATUSES
    if role not in statuses or status not in statuses[role]:
        raise ValueError(f"invalid {role} receipt status {status}")
    pinned = validate_checks(checks, pack=pack)[role]
    if not isinstance(payload, dict):
        raise ValueError("receipt must be an object")
    if not COMMIT.fullmatch(input_commit) or payload.get("input_commit") != input_commit:
        raise ValueError("receipt input_commit must match the assigned input commit")
    commit = required_text(payload, "commit", "receipt")
    if not COMMIT.fullmatch(commit):
        raise ValueError("receipt commit must be a full Git commit ID")
    if not isinstance(payload.get("issues"), str):
        raise ValueError("receipt issues must be a string")
    try:
        project = project.resolve(strict=True)
        store = store.resolve(strict=True)
        if (not store.is_dir() or store == project or project in store.parents
                or store.stat().st_mode & 0o077):
            raise ValueError("evidence store must be a private directory outside the worktree")
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot access the project or evidence store: {exc}") from exc
    require_clean = status not in {"blocked", "paused"}
    worktree(project, branch, input_commit, commit, require_clean=require_clean,
             allow_same_tree=(pack == "four" and status == "superseded"))

    rows = payload.get("checks")
    if not isinstance(rows, list):
        raise ValueError("receipt checks must be a list")
    reported = {}
    logs = {}
    evidence = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("receipt checks must contain objects")
        name = required_text(row, "name", "check")
        if name not in pinned or name in reported:
            raise ValueError(f"unregistered or duplicate {role} check {name}")
        check = pinned[name]
        if row.get("command") != check["command"]:
            raise ValueError(f"check {name} command must match pinned command")
        if row.get("commit") != commit:
            raise ValueError(f"check {name} commit must match receipt commit")
        code = row.get("exit_code")
        if type(code) is not int or not -255 <= code <= 255:
            raise ValueError(f"check {name} requires an integer exit_code")
        if {"log_snapshot", "patch_snapshot"} & row.keys():
            raise ValueError("check snapshot locations are assigned at acceptance")
        log = evidence_bytes(store, project, row, "log", f"check {name}")
        evidence.append((row, "log_snapshot", log))
        reported[name] = row
        logs[name] = log

    if status in {"done", "pass"}:
        if "failure" in payload:
            raise ValueError("passing receipt cannot include a failure")
        for name, check in pinned.items():
            if name not in reported:
                raise ValueError(f"missing required check {name}")
            if reported[name]["exit_code"] != check["exit_code"]:
                raise ValueError(f"check {name} must exit with code {check['exit_code']}")
    else:
        validate_failure(status, payload.get("failure"), pinned, reported, pack=pack)

    mutation_checks = [("mutation", "unit")]
    if pack == "four" and role == "architect":
        mutation_checks.append(("example-mutation", "acceptance"))
        if status == "pass":
            required_text(payload, "architecture", "Architect receipt")
    for name, baseline in mutation_checks:
        mutation = reported.get(name)
        if mutation is None or (status != "pass" and "patch" not in mutation):
            continue
        patch = evidence_bytes(store, project, mutation, "patch", f"{name} check")
        if status == "pass":
            changed = mutation_proof(project, commit, mutation, pinned[name], logs[baseline], logs[name], patch,
                                     example_path=spec_path if name == "example-mutation" else None)
            if name == "mutation" and spec_path in changed:
                raise ValueError("code mutation must leave the approved specification intact")
        else:
            replay_patch(project, commit, patch)
        evidence.append((mutation, "patch_snapshot", patch))

    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("receipt artifacts must be a list")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("commit") != commit:
            raise ValueError("artifact commit must match receipt commit")
        if "snapshot" in artifact:
            raise ValueError("artifact snapshot locations are assigned at acceptance")
        data = evidence_bytes(store, project, artifact, "path", "artifact")
        evidence.append((artifact, "snapshot", data))

    snapshots = [archived_path(store, data) for _, _, data in evidence]
    for (record, key, _), snapshot in zip(evidence, snapshots):
        record[key] = snapshot


def validate_failure(status, failure, pinned, reported, *, pack="two"):
    if not isinstance(failure, dict):
        raise ValueError(f"{status} receipt requires failure details")
    if status in {"blocked", "paused", "superseded"}:
        required_text(failure, "reason", status)
        return
    name = required_text(failure, "check", "failure")
    for key in ("input", "expected", "actual"):
        required_text(failure, key, "failure")
    if (name not in reported or (pack == "two" and name == "mutation")
            or reported[name]["exit_code"] == pinned[name]["exit_code"]):
        raise ValueError("failure must reference a pinned check with an unexpected exit code")
