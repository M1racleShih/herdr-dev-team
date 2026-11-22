"""Exercise four-pack through the installed entry point and a real unittest fixture."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from test_two_pack import FAKE_HC, INTERRUPT_CLI, SCRIPT


LEAD = "t11"
WORKERS = dict(specifier="t22", coder="t33", refactorer="t44", architect="t55")
EXAMPLES = "behavior/examples.json"
UNIT = "python3 -m unittest -v test_target"
ACCEPTANCE = UNIT + ".Target.test_approved_examples"
PROPERTIES = UNIT + ".Target.test_addition_property"
DEPENDENCIES = UNIT + ".Target.test_dependency_boundary"
CHECKS = {
    "specifier": [], "coder": [("unit", UNIT, 0), ("acceptance", ACCEPTANCE, 0)],
    "refactorer": [("unit", UNIT, 0), ("acceptance", ACCEPTANCE, 0), ("properties", PROPERTIES, 0)],
    "architect": [("unit", UNIT, 0), ("acceptance", ACCEPTANCE, 0), ("dependencies", DEPENDENCIES, 0),
                  ("mutation", UNIT, 1), ("example-mutation", ACCEPTANCE, 1)],
}
TEST_CASES = '''import ast
import json
from pathlib import Path
import unittest

from target import increment


class Target(unittest.TestCase):
    def test_approved_examples(self):
        cases = json.loads(Path("behavior/examples.json").read_text())
        for case in cases:
            self.assertEqual(increment(case["input"]), case["expected"], "approved increment example")

    def test_addition_property(self):
        for n in range(-5, 6):
            self.assertEqual(increment(n + 1) - increment(n), 1)

    def test_dependency_boundary(self):
        nodes = ast.walk(ast.parse(Path("target.py").read_text()))
        self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in nodes))
'''


class FourPackTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="four-pack test ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.project = self.root / "target"
        self.store = self.root / "store"
        self.database = self.store / "two-pack.sqlite3"
        self.sends = self.root / "transport.jsonl"
        transport = self.root / "hc"
        transport.write_text("#!/usr/bin/env python3\n" + FAKE_HC)
        transport.chmod(0o700)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}",
                        FAKE_SEND_LOG=str(self.sends), FAKE_TARGET=WORKERS["coder"],
                        FAKE_GATE=str(self.root / "gate"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        self.project.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Four Pack Test")
        self.git("config", "user.email", "four-pack@example.invalid")
        (self.project / "target.py").write_text("def increment(number):\n    return number\n")
        (self.project / "test_target.py").write_text(TEST_CASES)
        self.baseline = self.commit("Baseline")
        self.card = self.root / "card.md"
        self.card.write_text("# Increment\nAll examples are executable.\n")
        self.checks = self.json_file("checks.json", {
            role: [dict(name=name, command=command, exit_code=code) for name, command, code in checks]
            for role, checks in CHECKS.items()})

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.project, env=self.env, check=True,
                              capture_output=True, text=True, timeout=10).stdout

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def json_file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def cli(self, *args, caller=LEAD, code=0, pack="four", interrupt=None, mode="ok"):
        cmd = [sys.executable]
        if interrupt:
            cmd += ["-c", INTERRUPT_CLI, interrupt]
        result = subprocess.run([*cmd, str(SCRIPT), str(self.store), pack, *map(str, args)],
                                env=dict(self.env, HC_TERMINAL_ID=caller, FAKE_MODE=mode),
                                text=True, capture_output=True, timeout=15)
        expected = (code,) if isinstance(code, int) else code
        self.assertIn(result.returncode, expected, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout else result.stderr.strip()

    def init(self, path=EXAMPLES, **kwargs):
        workers = [arg for role, worker in WORKERS.items() for arg in (f"--{role}", worker)]
        return self.cli("init", "--project", self.project, "--card", self.card, "--checks", self.checks,
                        "--examples", path, *workers, **kwargs)

    def worker_sends(self):
        if not self.sends.exists():
            return []
        return [(target, message) for target, message in map(json.loads, self.sends.read_text().splitlines())
                if target in WORKERS.values()]

    def resume(self, run):
        return self.cli(run, "resume", caller="")

    def report(self, run, role, number, status, data, **kwargs):
        path = self.json_file(f"{role}-{number}-{status}.json", data)
        return self.cli(run, "report", role, number, status, "--result", path,
                        caller=WORKERS[role], **kwargs)

    def write_examples(self, expected=2):
        examples = self.project / EXAMPLES
        examples.parent.mkdir(exist_ok=True)
        examples.write_text(json.dumps([{"input": 1, "expected": expected}]) + "\n")
        return self.commit(f"Examples expect {expected}")

    def specifier(self, run, source, number=1, **kwargs):
        return self.report(run, "specifier", number, "done",
                           dict(input_commit=source, commit=self.head(), issues="", checks=[], examples=EXAMPLES),
                           **kwargs)

    def waiting(self):
        run = self.init()["id"]
        self.cli(run, "start")
        self.write_examples()
        self.specifier(run, self.baseline)
        return run, self.resume(run)["specification"]

    def approve(self, run, spec, **kwargs):
        return self.cli(run, "approve", spec["version"], spec["sha256"],
                        "--evidence", "Isolated test fixture: simulated human confirmed these exact examples", **kwargs)

    def checks_for(self, role, source, spec, *, issue=None):
        commit = self.head()
        rows = []
        for name, command, expected in CHECKS[role]:
            if name in ("mutation", "example-mutation") or (issue and name != issue):
                continue
            rows.append(self.check(self.project, role, name, command, commit, 1 if issue == name else expected))
        result = dict(input_commit=source, commit=commit, issues="", checks=rows,
                      specification={key: spec[key] for key in ("version", "sha256")})
        if issue:
            result["failure"] = dict(check=issue, input="1", expected="2", actual="1")
        return result

    def check(self, project, role, name, command, commit, expected):
        process = subprocess.run(command.split(), cwd=project, env=self.env,
                                 text=True, capture_output=True, timeout=10)
        output = process.stdout + process.stderr
        self.assertEqual(process.returncode, expected, output)
        log = self.store / f"{role}-{commit}-{name}.log"
        log.write_text(output)
        return dict(name=name, command=command, commit=commit, exit_code=process.returncode, log=log.name)

    def mutation(self, name, path, old, new, *, label=None):
        commit = self.head()
        clone = self.root / (label or name)
        self.git("clone", "--quiet", str(self.project), str(clone), cwd=self.root)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=clone).strip(), commit)
        target = clone / path
        body = target.read_text()
        self.assertEqual(body.count(old), 1)
        target.write_text(body.replace(old, new))
        patch = self.store / f"{label or name}.patch"
        patch.write_text(self.git("diff", cwd=clone))
        command = UNIT if name == "mutation" else ACCEPTANCE
        row = self.check(clone, label or "architect", name, command, commit, 1)
        return dict(row, patch=patch.name, test="test_target.Target.test_approved_examples",
                    assertion="approved increment example")

    def implement(self):
        (self.project / "target.py").write_text("def increment(number):\n    return number + 1\n")
        return self.commit("Implement increment")

    def test_wait_for_human_and_read_only_resume(self):
        run, spec = self.waiting()
        before = self.database.read_bytes(), sorted(os.listdir(self.store)), self.worker_sends()
        view = self.resume(run)
        self.assertEqual(view["phase"], "waiting-for-human")
        self.assertEqual(list(view["assignments"]), ["specifier:1"], "submission cannot assign Coder")
        self.assertEqual(spec["commit"], self.head())
        self.assertEqual(spec["path"], EXAMPLES)
        expected = (self.project / EXAMPLES).read_bytes()
        self.assertEqual(spec["body"], expected.decode())
        self.assertEqual(spec["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertIsNone(spec["approval"])
        self.assertEqual((self.database.read_bytes(), sorted(os.listdir(self.store)), self.worker_sends()), before)
        self.cli(run, "start")
        self.cli(run, "recover")
        self.card.write_text("# A registered draft does not approve examples.\n")
        self.cli(run, "revise", "--card", self.card)
        self.approve(run, spec, caller=WORKERS["specifier"], code=1)
        self.approve(run, dict(spec, sha256="0" * 64), code=1)
        self.approve(run, dict(spec, version=2), code=1)
        self.assertEqual(self.worker_sends(), before[2], "only an exact human approval may assign Coder")
        self.approve(run, spec)
        self.approve(run, spec)
        view = self.resume(run)
        self.assertEqual(list(view["assignments"]), ["specifier:1", "coder:1"])
        self.assertIn("simulated human", view["specification"]["approval"]["evidence"])
        self.assertEqual([target for target, _ in self.worker_sends()], list(WORKERS.values())[:2])

    def test_duplicate_approval_and_concurrent_recovery_dispatch_once(self):
        run, spec = self.waiting()
        self.approve(run, spec, interrupt="call", code=-signal.SIGKILL)
        self.assertEqual(self.resume(run)["assignments"]["coder:1"]["delivery"]["status"], "pending")
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(self.approve, run, spec, code=(0, 1)) if n % 2
                    else pool.submit(self.cli, run, "recover", code=(0, 1)) for n in range(6)]
            for job in jobs:
                job.result()
        view = self.resume(run)
        self.assertEqual([key for key in view["assignments"] if key.startswith("coder:")], ["coder:1"])
        self.assertEqual(len(view["assignments"]["coder:1"]["attempts"]), 1)
        self.assertEqual([target for target, _ in self.worker_sends()].count(WORKERS["coder"]), 1)

    def test_ambiguous_approval_delivery_is_not_resent(self):
        run, spec = self.waiting()
        self.approve(run, spec, mode="crash", code=-signal.SIGKILL)
        self.cli(run, "recover", code=1)
        self.approve(run, spec, code=1)
        self.assertEqual(self.resume(run)["assignments"]["coder:1"]["delivery"]["status"], "ambiguous")
        self.cli(run, "reconcile", "coder", 1, 1, "delivered", "--evidence", "Found original fixture assignment")
        self.cli(run, "recover")
        self.assertEqual([target for target, _ in self.worker_sends()].count(WORKERS["coder"]), 1)

    def test_role_order_final_commit_and_two_real_mutations(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        coder_commit = self.implement()
        self.report(run, "coder", 1, "done", self.checks_for("coder", spec["commit"], spec))
        self.assertEqual(self.resume(run)["current"], "refactorer:1", "Coder cannot complete the four-pack")
        self.report(run, "refactorer", 1, "done", self.checks_for("refactorer", coder_commit, spec))
        self.assertEqual(self.resume(run)["current"], "architect:1")
        (self.project / "target.py").write_text("def increment(number):\n    return 1 + number\n")
        final_commit = self.commit("Keep dependency-free implementation")
        report = self.checks_for("architect", coder_commit, spec)
        report["architecture"] = "Dependency-free core; verified with the pinned AST check."
        self.report(run, "architect", 1, "pass", report, code=1)
        self.assertEqual(self.resume(run)["phase"], "active", "missing mutation evidence cannot complete")
        code, examples = (self.mutation("mutation", "target.py", "1 + number", "2 + number"),
                          self.mutation("example-mutation", EXAMPLES, '"expected": 2', '"expected": 9'))
        report["checks"].extend((code, dict(examples, patch=code["patch"])))
        self.report(run, "architect", 1, "pass", report, code=1)
        report["checks"][-1] = dict(examples, commit=coder_commit)
        self.report(run, "architect", 1, "pass", report, code=1)
        report["checks"][-1] = self.mutation("example-mutation", EXAMPLES, '"expected": 2',
                                             '"expected":', label="invalid-json")
        rejection = self.report(run, "architect", 1, "pass", report, code=1)
        self.assertIn("failed with the declared assertion", rejection["rejection"])
        report["checks"][-1] = examples
        self.report(run, "architect", 1, "pass", report)
        view = self.resume(run)
        self.assertEqual(view["phase"], "completed")
        self.assertEqual(view["final_commit"], final_commit)
        self.assertEqual([target for target, _ in self.worker_sends()], list(WORKERS.values()))
        accepted = view["assignments"]["architect:1"]["receipt"]["result"]
        for check, assertion in zip(accepted["checks"][-2:], ("3 != 2", "2 != 9")):
            self.assertTrue((self.store / check["patch_snapshot"]).is_file())
            text = (self.store / check["log_snapshot"]).read_text()
            self.assertIn(f"AssertionError: {assertion} : approved increment example", text)

        before = self.worker_sends()
        self.write_examples(9)
        self.cli(run, "recover")
        self.assertEqual(self.resume(run), view, "later work cannot reopen an accepted commit")
        self.approve(run, spec)
        self.assertEqual(self.resume(run), view)
        self.assertEqual(self.worker_sends(), before)

    def yield_changed_examples(self, run, spec, **kwargs):
        payload = dict(input_commit=spec["commit"], commit=self.head(), issues="", checks=[],
                       specification={key: spec[key] for key in ("version", "sha256")},
                       failure={"reason": "The examples were changed; I have stopped working."})
        return self.report(run, "coder", 1, "superseded", payload, **kwargs)

    def test_changed_examples_wait_for_inflight_send_and_worker_yield(self):
        run, spec = self.waiting()
        with socket.socket(socket.AF_UNIX) as listener, ThreadPoolExecutor() as pool:
            listener.bind(self.env["FAKE_GATE"])
            listener.listen()
            listener.settimeout(10)
            original = pool.submit(self.approve, run, spec, mode="hold")
            connection, _ = listener.accept()
            with connection:
                try:
                    self.write_examples(1)
                    self.cli(run, "recover", code=(0, 1))
                    self.assertEqual([target for target, _ in self.worker_sends()], list(WORKERS.values())[:2],
                                     "replacement cannot be sent while the original send can still arrive")
                    self.assertEqual(self.resume(run)["current"], "coder:1")
                finally:
                    connection.sendall(b"!")
            original.result()
        self.cli(run, "recover")
        self.assertEqual(self.resume(run)["current"], "coder:1", "delivery alone does not yield the worktree")
        self.yield_changed_examples(run, spec)
        view = self.resume(run)
        self.assertEqual(view["current"], "specifier:2")
        self.assertEqual(view["assignments"]["coder:1"]["receipt"]["status"], "superseded")
        self.assertEqual([target for target, _ in self.worker_sends()], [WORKERS["specifier"], WORKERS["coder"],
                                                                       WORKERS["specifier"]])

    def test_superseded_accepts_clean_same_tree_after_committed_example_revert(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        self.yield_changed_examples(run, spec, code=1)
        self.write_examples(9)
        reverted = self.write_examples(2)
        self.assertEqual(self.git("rev-parse", f"{reverted}^{{tree}}"),
                         self.git("rev-parse", f"{spec['commit']}^{{tree}}"))
        self.cli(run, "recover")
        self.assertEqual(self.resume(run)["current"], "coder:1")
        self.yield_changed_examples(run, spec)
        item = self.resume(run)["assignments"]["specifier:2"]
        self.assertEqual(item["input_commit"], reverted)
        self.assertEqual(item["delivery"]["status"], "delivered")

    def test_changed_examples_reconcile_nondelivery_without_retrying_old_work(self):
        run, spec = self.waiting()
        self.approve(run, spec, mode="crash", code=-signal.SIGKILL)
        self.write_examples(1)
        self.cli(run, "recover", code=(0, 1))
        self.assertEqual(self.resume(run)["current"], "coder:1")
        self.cli(run, "reconcile", "coder", 1, 1, "retry", "--evidence", "Fixture send proved no delivery")
        view = self.resume(run)
        self.assertEqual(view["current"], "specifier:2")
        self.assertEqual(view["assignments"]["coder:1"]["attempts"][0]["status"], "not-delivered-by-inspection")
        self.assertEqual([target for target, _ in self.worker_sends()].count(WORKERS["coder"]), 1)

    def test_revision_rejects_stale_receipts_and_approval(self):
        run, old = self.waiting()
        self.approve(run, old)
        commit = self.write_examples(1)
        report = self.checks_for("coder", old["commit"], old)
        self.report(run, "coder", 1, "done", report, code=1)
        self.assertEqual(self.resume(run)["current"], "coder:1")
        self.approve(run, old, code=1)
        self.yield_changed_examples(run, old)
        self.assertEqual(self.resume(run)["current"], "specifier:2")
        self.report(run, "coder", 1, "done", report, code=1)
        self.specifier(run, commit, 2)
        view = self.resume(run)
        self.assertEqual(len(view["specification_versions"]), 2)
        self.assertEqual(view["specification_versions"][0]["sha256"], old["sha256"])
        self.assertIsNotNone(view["specification_versions"][0]["approval"])
        self.assertIsNone(view["specification"]["approval"])
        self.approve(run, old, code=1)
        self.assertNotIn("coder:2", self.resume(run)["assignments"])
        self.approve(run, view["specification"])
        self.assertEqual(self.resume(run)["assignments"]["coder:2"]["input_commit"], commit)

    def test_approval_of_changed_unapproved_examples_requires_a_new_version(self):
        run, old = self.waiting()
        changed = self.write_examples(9)
        self.approve(run, old, code=1)
        self.assertEqual(self.resume(run)["current"], "specifier:2")
        self.assertEqual([target for target, _ in self.worker_sends()], [WORKERS["specifier"]] * 2)
        self.specifier(run, changed, 2)
        spec = self.resume(run)["specification"]
        self.assertNotEqual(spec["sha256"], old["sha256"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(lambda _: self.approve(run, spec, code=(0, 1)), range(4)):
                self.assertTrue(result["approved"])
        self.assertEqual([target for target, _ in self.worker_sends()].count(WORKERS["coder"]), 1)

    def test_recover_invalidates_changed_examples_before_pending_delivery(self):
        run, spec = self.waiting()
        self.approve(run, spec, interrupt="call", code=-signal.SIGKILL)
        self.write_examples(9)
        self.write_examples(2)
        self.cli(run, "recover")
        view = self.resume(run)
        self.assertEqual(view["current"], "specifier:2")
        self.assertEqual(view["assignments"]["coder:1"]["attempts"], [])
        self.assertNotIn(WORKERS["coder"], [target for target, _ in self.worker_sends()])

    def test_replacement_waits_for_latest_clean_head(self):
        run, spec = self.waiting()
        self.approve(run, spec, interrupt="call", code=-signal.SIGKILL)
        self.write_examples(9)
        (self.project / "notes.txt").write_text("Ordinary uncommitted edit\n")
        self.assertIn("clean", self.cli(run, "recover", code=1))
        self.assertEqual(list(self.resume(run)["assignments"]), ["specifier:1", "coder:1"],
                         "a dirty HEAD must not reserve a replacement assignment")
        clean_head = self.commit("Preserve work before the new Specifier starts")
        self.cli(run, "recover")
        view = self.resume(run)
        self.assertEqual(view["current"], "specifier:2")
        self.assertEqual(view["assignments"]["specifier:2"]["input_commit"], clean_head)
        self.assertEqual(view["assignments"]["specifier:2"]["delivery"]["status"], "delivered")
        self.assertEqual(view["assignments"]["coder:1"]["attempts"], [])

    def test_replacement_cannot_discard_active_or_paused_commits(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        coder_commit = self.implement()
        self.report(run, "coder", 1, "done", self.checks_for("coder", spec["commit"], spec))
        (self.project / "notes.txt").write_text("Preserve Refactorer's in-progress work\n")
        pause_commit = self.commit("Refactorer notes before pause")
        data = dict(input_commit=coder_commit, commit=pause_commit, issues="blocked", checks=[],
                    failure={"reason": "Need Lead input"},
                    specification={key: spec[key] for key in ("version", "sha256")})
        self.report(run, "refactorer", 1, "paused", data)
        branch = self.git("branch", "--show-current").strip()
        self.git("branch", "-m", "preserved-history")
        self.git("switch", "-c", branch, coder_commit)
        self.write_examples(9)
        self.assertIn("descend", self.cli(run, "recover", code=1))
        self.assertEqual(self.resume(run)["current"], "refactorer:1")
        self.git("switch", "preserved-history")
        self.git("branch", "-M", branch)
        preserved = self.write_examples(9)
        self.cli(run, "recover")
        self.assertEqual(self.resume(run)["assignments"]["specifier:2"]["input_commit"], preserved)

    def test_failed_check_reworks_through_coder_and_keeps_approval(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        commit = self.implement()
        self.report(run, "coder", 1, "done", self.checks_for("coder", spec["commit"], spec))
        self.git("commit", "--allow-empty", "-m", "Check-only must not invent a commit")
        rejection = self.report(run, "refactorer", 1, "done", self.checks_for("refactorer", commit, spec), code=1)
        self.assertIn("check-only receipts must use the assigned input commit", rejection["rejection"])
        (self.project / "target.py").write_text("def increment(number):\n    return number\n")
        failed_commit = self.commit("Introduce regression")
        self.report(run, "refactorer", 1, "fail", self.checks_for("refactorer", commit, spec, issue="acceptance"))
        view = self.resume(run)
        self.assertEqual(view["current"], "coder:2")
        self.assertEqual(view["specification"]["sha256"], spec["sha256"])
        self.assertIsNotNone(view["specification"]["approval"])
        self.assertEqual(view["assignments"]["coder:2"]["input_commit"], failed_commit)
        (self.project / "target.py").write_text("def increment(number):\n    return 1 + number\n")
        fix = self.commit("Repair regression")
        self.report(run, "coder", 2, "done", self.checks_for("coder", failed_commit, spec))
        view = self.resume(run)
        self.assertEqual(view["assignments"]["refactorer:2"]["input_commit"], fix)
        self.assertEqual(view["assignments"]["refactorer:1"]["receipt"]["status"], "fail")

    def test_card_activation_and_paused_rework_do_not_approve_examples(self):
        run, old = self.waiting()
        self.card.write_text("# Updated acceptance details\n")
        self.cli(run, "revise", "--card", self.card, "--approve-evidence", "Isolated test card approval")
        view = self.resume(run)
        self.assertEqual(view["current"], "specifier:2")
        self.assertIsNone(view["specification"])
        self.approve(run, old, code=1)
        self.specifier(run, old["commit"], 2)
        spec = self.resume(run)["specification"]
        self.assertIsNone(spec["approval"])
        self.approve(run, spec)
        data = dict(input_commit=spec["commit"], commit=spec["commit"], issues="blocked", checks=[],
                    failure={"reason": "Dependency missing"},
                    specification={key: spec[key] for key in ("version", "sha256")})
        self.report(run, "coder", 2, "blocked", data)
        self.cli(run, "recover")
        self.assertEqual(self.resume(run)["phase"], "blocked")
        self.cli(run, "rework")
        view = self.resume(run)
        self.assertEqual(view["current"], "coder:3")
        self.assertNotIn("refactorer:2", view["assignments"])
        self.assertEqual(view["assignments"]["coder:3"]["specification"], data["specification"])
        self.assertEqual(view["assignments"]["coder:2"]["receipt"]["status"], "blocked")

    def test_historical_two_store_is_read_only_and_shared(self):
        checks = self.json_file("two.json", {"coder": [dict(name="unit", command=UNIT, exit_code=0)],
            "cleaner": [dict(name="unit", command=UNIT, exit_code=0), dict(name="mutation", command=UNIT, exit_code=1)]})
        historical = self.cli("init", "--project", self.project, "--card", self.card, "--checks", checks,
                              "--coder", "t66", "--cleaner", "t77", pack="two")["id"]
        with sqlite3.connect(self.database) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(names, {"runs", "cards", "assignments", "attempts", "receipts"})
        self.cli(historical, "start", pack="two")
        before = self.database.read_bytes()
        historical_view = self.cli(historical, "resume", pack="two", caller="")
        self.cli(historical, "resume", caller="", code=1)
        self.assertEqual(self.database.read_bytes(), before)
        new_run = self.init()["id"]
        self.assertEqual(self.cli(historical, "resume", pack="two", caller=""), historical_view)
        self.cli(new_run, "resume", pack="two", caller="", code=1)
        self.assertEqual(self.resume(new_run)["pack"], "four")

    def test_only_normalized_committed_regular_examples_are_submitted(self):
        self.assertIn("project-relative", self.init("../examples.json", code=1))
        run = self.init()["id"]
        self.cli(run, "start")
        self.specifier(run, self.baseline, code=1)
        path = self.project / EXAMPLES
        path.parent.mkdir()
        path.symlink_to("../test_target.py")
        self.commit("Symlink instead of example file")
        self.specifier(run, self.baseline, code=1)
        self.assertEqual(self.resume(run)["current"], "specifier:1")
        self.assertEqual(len(self.worker_sends()), 1)
