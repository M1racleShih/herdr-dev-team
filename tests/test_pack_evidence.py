"""Exercise receipt evidence against real Git worktrees and unittest output."""

from copy import deepcopy
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/herdr-dev-team/scripts"
sys.path.insert(0, str(SCRIPTS))
from pack_evidence import validate, validate_checks


UNIT_COMMAND = "python3 -m unittest discover -v"
ASSERTION = "'completed' != 'assigned'"
TEST_ID = "test_app.SummaryTest.test_dispatched"
PINNED = {
    "coder": [{"name": "unit", "command": UNIT_COMMAND, "exit_code": 0}],
    "cleaner": [
        {"name": "unit", "command": UNIT_COMMAND, "exit_code": 0},
        {"name": "mutation", "command": UNIT_COMMAND, "exit_code": 1,
         "test": TEST_ID, "assertion": ASSERTION},
    ],
}


class PackEvidenceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pack evidence ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "source repository"
        self.project = self.root / "task worktree"
        self.store = self.root / "private store"
        self.store.mkdir(mode=0o700)
        self.repo.mkdir()
        self.branch = "pilot"
        self.git("init", "-q", self.repo, project=self.root)
        self.git("config", "user.email", "fixture@example.invalid", project=self.repo)
        self.git("config", "user.name", "Pack Test", project=self.repo)
        self.write(self.repo / "app.py", "def summary():\n    return 'assigned'\n")
        self.write(self.repo / "test_app.py", """
import unittest
from app import summary

class SummaryTest(unittest.TestCase):
    def test_dispatched(self):
        self.assertEqual(summary(), 'assigned')
""".lstrip())
        self.git("add", ".", project=self.repo)
        self.git("commit", "-qm", "Baseline", project=self.repo)
        self.git("worktree", "add", "-qb", self.branch, self.project, project=self.repo)
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args, project=None):
        result = subprocess.run(
            ["git", "-C", str(project or self.project), *map(str, args)],
            capture_output=True, check=True, text=True,
        )
        return result.stdout

    def write(self, path, value):
        path.write_text(value, encoding="utf-8")
        return path

    def check(self, name, commit, *, path=None, command=UNIT_COMMAND, code=0):
        return {"name": name, "command": command, "commit": commit, "exit_code": code,
                "log": str(path or self.write(self.store / f"{name}.log", "check output\n"))}

    def payload(self, *checks, input_commit=None, commit=None, **fields):
        return {"input_commit": input_commit or self.base, "commit": commit or self.base,
                "issues": "", "checks": list(checks), **fields}

    def validate(self, payload, *, input_commit=None, role="coder", status="done", checks=None,
                 branch=None, project=None):
        return validate(
            project or self.project, branch or self.branch, input_commit or self.base,
            role, status, payload, self.store, checks or PINNED,
        )

    def run_check(self, name, project, command=UNIT_COMMAND):
        result = subprocess.run(
            shlex.split(command), cwd=project, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, check=False,
        )
        return result.returncode, self.write(self.store / name, result.stdout + result.stderr)

    def mutation_rows(self):
        green, original_log = self.run_check("original.log", self.project)
        mutant = self.root / "mutant"
        shutil.copytree(self.project, mutant, ignore=shutil.ignore_patterns(".git"))
        self.write(mutant / "app.py", "def summary():\n    return 'completed'\n")
        self.write(self.project / "app.py", (mutant / "app.py").read_text(encoding="utf-8"))
        patch = self.write(self.store / "mutation.patch", self.git("diff", "--", "app.py"))
        self.git("restore", "--", "app.py")
        red, mutant_log = self.run_check("mutant.log", mutant)
        mutation = self.check("mutation", self.base, path=mutant_log, code=red)
        mutation.update(patch=patch.name, test=TEST_ID, assertion=ASSERTION)
        return self.check("unit", self.base, path=original_log, code=green), mutation

    def test_check_only_and_changed_receipts_keep_snapshot(self):
        log = self.write(self.store / "original.log", "original result\n")
        unchanged = self.payload(self.check("unit", self.base, path=log.name))
        self.validate(unchanged)
        saved = self.store / unchanged["checks"][0]["log_snapshot"]
        self.write(log, "result later overwritten\n")
        self.assertEqual(saved.read_text(encoding="utf-8"), "original result\n")

        self.write(self.project / "extra.py", "answer = 42\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Add answer")
        head = self.git("rev-parse", "HEAD").strip()
        self.validate(self.payload(self.check("unit", head), commit=head))

    def test_rejects_incorrect_checkout_and_dirty_worktree(self):
        payload = self.payload(self.check("unit", self.base))
        with self.assertRaisesRegex(ValueError, "assigned worktree must be on branch"):
            self.validate(payload, branch="wrong")
        nested = self.project / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "root"):
            self.validate(payload, project=nested)
        for changed_path in (self.project / "untracked", self.project / "app.py"):
            with self.subTest(changed_path=changed_path):
                self.write(changed_path, "dirty\n")
                with self.assertRaisesRegex(ValueError, "uncommitted"):
                    self.validate(payload)
                if changed_path.name == "untracked":
                    changed_path.unlink()
                else:
                    self.git("restore", "--", changed_path.name)
        self.git("commit", "--allow-empty", "-qm", "Empty commit")
        with self.assertRaisesRegex(ValueError, "HEAD"):
            self.validate(payload)
        head = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(ValueError, "check-only"):
            self.validate(self.payload(self.check("unit", head), commit=head))

    def test_rejects_commit_outside_input_history(self):
        unrelated = self.git("commit-tree", "HEAD^{tree}", "-m", "Unrelated commit").strip()
        payload = self.payload(self.check("unit", self.base), input_commit=unrelated)
        with self.assertRaisesRegex(ValueError, "descend"):
            self.validate(payload, input_commit=unrelated)

    def test_requires_pinned_command_expected_exit_and_nonempty_private_log(self):
        blank = self.write(self.store / "blank.log", " \n")
        escape = self.store / "escape"
        escape.symlink_to(self.project / "app.py")
        for override, error in (
            ({"command": "something else"}, "pinned command"),
            ({"exit_code": 1}, "must exit"),
            ({"commit": "old"}, "match receipt commit"),
            ({"log": blank.name}, "is empty"),
            ({"log": escape.name}, "private store outside"),
        ):
            with self.subTest(override=override):
                row = self.check("unit", self.base)
                row.update(override)
                with self.assertRaisesRegex(ValueError, error):
                    self.validate(self.payload(row))
        with self.assertRaisesRegex(ValueError, "missing required check unit"):
            self.validate(self.payload())
        bad_pins = deepcopy(PINNED)
        bad_pins["cleaner"][1]["command"] = "echo unrelated"
        with self.assertRaisesRegex(ValueError, "same command"):
            validate_checks(bad_pins)

    def test_accepts_actual_mutation_logs_and_replays_patch_in_isolation(self):
        green, red = self.mutation_rows()
        self.validate(self.payload(green, red), role="cleaner", status="pass")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.store / red["patch_snapshot"]).read_text(encoding="utf-8"),
                         (self.store / red["patch"]).read_text(encoding="utf-8"))

    def test_rejects_assertion_from_a_different_test_and_unreplayable_patch(self):
        green, red = self.mutation_rows()
        mutant_log = Path(red["log"])
        original = mutant_log.read_text(encoding="utf-8")
        self.write(mutant_log, original.replace(ASSERTION, "something else") + f"""
======================================================================
FAIL: test_other (test_app.SummaryTest.test_other)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: {ASSERTION}
""")
        with self.assertRaisesRegex(ValueError, "declared assertion"):
            self.validate(self.payload(green, red), role="cleaner", status="pass")
        self.write(mutant_log, original)
        patch = self.store / red["patch"]
        self.write(patch, patch.read_text(encoding="utf-8").replace("'assigned'", "'previous'"))
        with self.assertRaisesRegex(ValueError, "cannot be replayed"):
            self.validate(self.payload(green, red), role="cleaner", status="pass")

    def test_accepts_rework_evidence_with_independent_check(self):
        checks = deepcopy(PINNED)
        command = """python3 -c 'from app import summary; assert summary() == "completed", summary()'"""
        checks["cleaner"].append({"name": "acceptance", "command": command, "exit_code": 0})
        code, log = self.run_check("failure.log", self.project, command)
        acceptance = self.check("acceptance", self.base, command=command, code=code, path=log)
        failure = {"check": "acceptance", "input": "Dispatched worktree", "expected": "completed",
                   "actual": "assigned"}
        self.validate(self.payload(acceptance, failure=failure), role="cleaner",
                      status="fail", checks=checks)
        self.assertIn("log_snapshot", acceptance)

    def test_dirty_worktree_can_report_blocked_or_paused(self):
        self.write(self.project / "local-edit", "in progress\n")
        for role, status in (("coder", "blocked"), ("cleaner", "paused")):
            with self.subTest(role=role, status=status):
                self.validate(self.payload(failure={"reason": "Need expected behavior clarified"}),
                              role=role, status=status)
                with self.assertRaisesRegex(ValueError, "failure details"):
                    self.validate(self.payload(), role=role, status=status)
