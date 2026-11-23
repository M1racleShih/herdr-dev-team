"""Exercise two-pack through its CLI in a linked worktree with isolated Core sends."""

from contextlib import closing, contextmanager
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


SCRIPT = Path(__file__).resolve().parents[1] / "skills/herdr-dev-team/scripts/handoff.py"
LEADER, CODER, CLEANER = "t11", "t22", "t33"
CODER_COMMAND = "python3 -m unittest -v test_coder"
CLEANER_COMMAND = "python3 -m unittest -v test_cleaner"
MUTATION_TEST = "test_cleaner.SummaryTest.test_summary"
MUTATION_ASSERTION = "the summary must report the recorded progress"
CHECKS = {
    "coder": [{"name": "unit", "command": CODER_COMMAND, "exit_code": 0}],
    "cleaner": [{"name": "unit", "command": CLEANER_COMMAND, "exit_code": 0},
                {"name": "mutation", "command": CLEANER_COMMAND, "exit_code": 1}],
}
CODER_TEST = '''from pathlib import Path
import unittest


class FileTest(unittest.TestCase):
    def test_output_exists(self):
        self.assertTrue(Path("tracked.txt").read_text().strip())
'''
CLEANER_TEST = f'''from pathlib import Path
import unittest


class SummaryTest(unittest.TestCase):
    def test_summary(self):
        self.assertNotIn(Path("tracked.txt").read_text().strip(), ("Incorrect", "Mutated"),
                         {MUTATION_ASSERTION!r})
'''

FAKE_HC = r'''
import json
import os
from pathlib import Path
import signal
import socket
import sys

command, format_flag, target, message = sys.argv[1:]
assert (command, format_flag) == ("send", "--json")
with Path(os.environ["FAKE_SEND_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps([target, message]) + "\n")
if target == os.environ["FAKE_TARGET"]:
    mode = os.environ["FAKE_MODE"]
    if mode == "crash":
        os.kill(os.getppid(), signal.SIGKILL)
    if mode == "hold":
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gate:
            gate.connect(os.environ["FAKE_GATE"])
            gate.settimeout(10)
            assert gate.recv(1) == b"!"
print(json.dumps({"turn": 11, "started": True}))
'''

# Use the CLI unchanged and stop its subprocess at the dispatch boundaries.
INTERRUPT_CLI = r'''
from pathlib import Path
import os
import runpy
import signal
import sys

event, *sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))

def dispatch_trace(frame, trace_event, value):
    if trace_event == event:
        os.kill(os.getpid(), signal.SIGKILL)
    return dispatch_trace

def trace(frame, trace_event, value):
    if frame.f_code.co_name == "dispatch" and Path(frame.f_code.co_filename).name == "two_pack.py":
        if trace_event == event:
            os.kill(os.getpid(), signal.SIGKILL)
        return dispatch_trace(frame, trace_event, value)

sys.settrace(trace)
runpy.run_path(sys.argv[0], run_name="__main__")
'''


class TwoPackTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="two-pack test ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.repository = self.root / "source repo"
        self.project = self.root / "target worktree"
        self.store = self.root / "private store"
        self.database = self.store / "two-pack.sqlite3"
        self.send_log = self.root / "transport.jsonl"
        self.gate = self.root / "gate"
        fake_hc = self.file("hc", "#!/usr/bin/env python3\n" + FAKE_HC)
        fake_hc.chmod(0o700)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}",
                        FAKE_SEND_LOG=str(self.send_log), FAKE_GATE=str(self.gate),
                        FAKE_TARGET=CLEANER, FAKE_MODE="ok", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "--quiet", self.repository, cwd=self.root)
        self.git("config", "user.name", "Two Pack Test", cwd=self.repository)
        self.git("config", "user.email", "two-pack@example.invalid", cwd=self.repository)
        (self.repository / "tracked.txt").write_text("Baseline\n", encoding="utf-8")
        (self.repository / "test_coder.py").write_text(CODER_TEST, encoding="utf-8")
        (self.repository / "test_cleaner.py").write_text(CLEANER_TEST, encoding="utf-8")
        self.git("add", "tracked.txt", "test_coder.py", "test_cleaner.py", cwd=self.repository)
        self.git("commit", "--quiet", "-m", "Baseline", cwd=self.repository)
        self.git("worktree", "add", "--quiet", "-b", "delivery", self.project, cwd=self.repository)
        self.baseline = self.git("rev-parse", "HEAD")
        self.card = self.file("card.md", f"# Target\nBaseline {self.baseline}\nThe checks must pass.\n")
        self.checks = self.json_file("checks.json", CHECKS)

    def file(self, name, content):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def json_file(self, name, value):
        return self.file(name, json.dumps(value))

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *map(str, args)], cwd=cwd or self.project,
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        (self.project / "tracked.txt").write_text(f"{message}\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def argv(self, *args, interrupt=None):
        command = [sys.executable]
        if interrupt is not None:
            command.extend(["-c", INTERRUPT_CLI, interrupt])
        return [*command, str(SCRIPT), str(self.store), "two", *map(str, args)]

    def cli(self, *args, caller=LEADER, mode="ok", interrupt=None, exit_code=0):
        result = subprocess.run(self.argv(*args, interrupt=interrupt), text=True,
                                capture_output=True, timeout=10,
                                env=dict(self.env, HC_TERMINAL_ID=caller, FAKE_MODE=mode))
        self.assertEqual(result.returncode, exit_code, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout else result.stderr.strip()

    def init(self, *extra):
        return self.cli("init", "--project", self.project, "--card", self.card,
                        "--coder", CODER, "--cleaner", CLEANER, "--checks", self.checks, *extra)

    def start(self, *extra):
        run = self.init(*extra)["id"]
        self.cli(run, "start")
        return run

    def resume(self, run):
        return self.cli(run, "resume", caller="")

    def report_argv(self, run, role, number, status, result):
        return (run, "report", role, number, status, "--result", result)

    def report(self, run, role, number, status, result, **kwargs):
        kwargs.setdefault("caller", {"coder": CODER, "cleaner": CLEANER}[role])
        return self.cli(*self.report_argv(run, role, number, status, result), **kwargs)

    def evidence(self, name, content):
        directory = self.store / "fixtures"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.store))

    def check(self, name, command, prefix, commit, expected):
        result = subprocess.run(command.split(), cwd=self.project, env=self.env,
                                text=True, capture_output=True, timeout=10)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        return {"name": name, "command": command, "exit_code": result.returncode,
                "commit": commit, "log": self.evidence(f"{prefix}-{name}.log", output)}

    def result_data(self, role, status, input_commit, prefix):
        commit = self.git("rev-parse", "HEAD")
        data = {"input_commit": input_commit, "commit": commit, "issues": "Fixture result",
                "checks": []}
        checks = data["checks"]
        if status in ("paused", "blocked"):
            data["failure"] = {"reason": "Task card wording needs Delivery Lead clarification"}
        elif role == "coder":
            checks.append(self.check("unit", CODER_COMMAND, prefix, commit, expected=0))
        elif status == "fail":
            checks.append(self.check("unit", CLEANER_COMMAND, prefix, commit, expected=1))
            data["failure"] = {"check": "unit", "input": "Incorrect",
                               "expected": "Report recorded progress", "actual": "Misreported progress"}
        else:
            checks.append(self.check("unit", CLEANER_COMMAND, prefix, commit, expected=0))
            tracked = self.project / "tracked.txt"
            original = tracked.read_bytes()
            try:
                tracked.write_text("Mutated\n", encoding="utf-8")
                patch = self.evidence(f"{prefix}-mutant.patch", self.git("diff", "--", "tracked.txt") + "\n")
                mutant = self.check("mutation", CLEANER_COMMAND, prefix, commit, expected=1)
            finally:
                tracked.write_bytes(original)
            checks.append(dict(mutant, patch=patch, test=MUTATION_TEST, assertion=MUTATION_ASSERTION))
        return data

    def result(self, role="coder", status="done", *, input_commit=None, prefix=None):
        prefix = prefix or f"{role}-{status}"
        return self.json_file(f"{prefix}.json", self.result_data(role, status, input_commit or self.baseline, prefix))

    def sends(self):
        if not self.send_log.exists():
            return []
        return [json.loads(line) for line in self.send_log.read_text(encoding="utf-8").splitlines()]

    def worker_sends(self):
        return [(target, message) for target, message in self.sends() if target in (CODER, CLEANER)]

    def assert_worker_targets(self, *targets):
        self.assertEqual([target for target, _ in self.worker_sends()], list(targets))

    def rows(self, sql, *params):
        with closing(sqlite3.connect(f"{self.database.as_uri()}?mode=ro", uri=True)) as conn:
            return conn.execute(sql, params).fetchall()

    def assignment_count(self, run, role):
        return self.rows("SELECT COUNT(*) FROM assignments WHERE run_id = ? AND role = ?", run, role)[0][0]

    def attempt_count(self, assignment):
        return self.rows("SELECT COUNT(*) FROM attempts WHERE assignment_id = ?", assignment["id"])[0][0]

    def assert_readonly_resume(self, run):
        before = {path.name: (path.stat().st_mtime_ns, path.read_bytes())
                  for path in self.store.iterdir() if path.is_file()}
        sends = self.sends()
        state = self.resume(run)
        self.assertEqual(state, self.resume(run))
        self.assertEqual(before, {path.name: (path.stat().st_mtime_ns, path.read_bytes())
                                  for path in self.store.iterdir() if path.is_file()})
        self.assertEqual(self.sends(), sends)
        return state

    @contextmanager
    def hold_cleaner_send(self, args):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.gate))
            server.listen()
            server.settimeout(10)
            with subprocess.Popen(self.argv(*args), text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  env=dict(self.env, HC_TERMINAL_ID=CODER, FAKE_MODE="hold")) as first:
                try:
                    gate, _ = server.accept()
                    with gate:
                        try:
                            yield
                        finally:
                            gate.sendall(b"!")
                    stdout, stderr = first.communicate(timeout=10)
                    self.assertEqual(first.returncode, 0, (stdout, stderr))
                finally:
                    if first.poll() is None:
                        first.kill()
                        first.communicate(timeout=10)

    def test_project_db_preserves_multiple_runs_and_their_original_cards(self):
        first = self.init()["id"]
        original = self.assert_readonly_resume(first)
        self.assertEqual(original["phase"], "ready")
        self.assertEqual(original["summary"], "等待向 Coder 派工。")
        self.assertEqual(original["project"], str(self.project))
        self.assertEqual(original["branch"], "delivery")
        self.assertEqual(original["baseline"], self.baseline)
        self.assertEqual(original["checks"], CHECKS)
        self.assertEqual(original["card"]["body"], self.card.read_text(encoding="utf-8"))
        self.assertEqual(self.store.stat().st_mode & 0o077, 0)

        newer_commit = self.commit("Next baseline")
        self.card.write_text(f"# A new target\nBaseline {newer_commit}\n", encoding="utf-8")
        second = self.init()["id"]
        current = self.assert_readonly_resume(second)
        self.assertNotEqual(first, second)
        self.assertEqual(original["project_id"], current["project_id"])
        self.assertEqual(current["baseline"], newer_commit)
        self.assertNotEqual(original["card"]["body"], current["card"]["body"])
        self.assertEqual(self.assert_readonly_resume(first), original)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM runs"), [(2,)])
        self.assertEqual(self.sends(), [])

    def test_changed_commits_advance_automatically_and_duplicate_receipts_are_idempotent(self):
        run = self.start()
        coder_commit = self.commit("Coder delivery")
        coder_result = self.result()
        accepted = self.report(run, "coder", 1, "done", coder_result)
        self.assertTrue(accepted["receipt_saved"])
        state = self.assert_readonly_resume(run)
        self.assertEqual(state["current"], "cleaner:1")
        cleaner = state["assignments"]["cleaner:1"]
        self.assertEqual(cleaner["input_commit"], coder_commit)
        self.assertEqual(cleaner["delivery"]["status"], "delivered")
        self.assertEqual(state["summary"],
                         f"第 1 轮，Coder 已交付 {coder_commit[:7]}：已向 Cleaner 派工，尚无 Cleaner 完成回执。")
        self.assertIn(cleaner["id"], self.worker_sends()[-1][1])
        self.assertIn(coder_commit, self.worker_sends()[-1][1])
        sends = self.sends()
        duplicate = self.report(run, "coder", 1, "done", coder_result)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.sends(), sends)
        coder = state["assignments"]["coder:1"]
        self.assertEqual(self.rows("SELECT COUNT(*) FROM receipts WHERE assignment_id = ?", coder["id"]), [(1,)])
        self.assertEqual(self.assignment_count(run, "cleaner"), 1)
        self.assertEqual(self.attempt_count(cleaner), 1)

        cleaner_commit = self.commit("Cleaner improvement")
        cleaner_result = self.result("cleaner", "pass", input_commit=coder_commit)
        accepted = self.report(run, "cleaner", 1, "pass", cleaner_result)
        self.assertTrue(accepted["receipt_saved"])
        complete = self.assert_readonly_resume(run)
        self.assertEqual(complete["phase"], "completed")
        self.assertEqual(complete["assignments"]["cleaner:1"]["receipt"]["result"]["commit"], cleaner_commit)
        self.assertEqual(complete["summary"],
                         f"第 1 轮，Cleaner 已在 {cleaner_commit[:7]} 验收通过，two-pack 已完成。")
        sends = self.sends()
        duplicate = self.report(run, "cleaner", 1, "pass", cleaner_result)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.sends(), sends)
        self.assert_worker_targets(CODER, CLEANER)

    def test_check_only_accepts_both_roles_on_the_original_commit(self):
        run = self.start()
        self.cli(run, "start")
        self.report(run, "coder", 1, "done", self.result())
        self.report(run, "cleaner", 1, "pass", self.result("cleaner", "pass"))
        state = self.assert_readonly_resume(run)
        self.assertEqual(state["phase"], "completed")
        for key in ("coder:1", "cleaner:1"):
            receipt = state["assignments"][key]["receipt"]["result"]
            self.assertEqual(receipt["input_commit"], self.baseline)
            self.assertEqual(receipt["commit"], self.baseline)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.baseline)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assert_worker_targets(CODER, CLEANER)

    def test_concurrent_duplicate_reports_and_recover_share_one_cleaner_claim(self):
        run = self.start()
        args = self.report_argv(run, "coder", 1, "done", self.result())
        with self.hold_cleaner_send(args):
            state = self.assert_readonly_resume(run)
            cleaner = state["assignments"]["cleaner:1"]
            self.assertIn("receipt", state["assignments"]["coder:1"])
            self.assertEqual(cleaner["delivery"]["status"], "intent")
            duplicate = self.cli(*args, caller=CODER, exit_code=1)
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["dispatch"]["assignment"], cleaner["id"])
            self.cli(run, "recover", exit_code=1)
            self.assertEqual(self.assignment_count(run, "cleaner"), 1)
            self.assertEqual(self.attempt_count(cleaner), 1)
            self.assert_worker_targets(CODER, CLEANER)
        cleaner = self.assert_readonly_resume(run)["assignments"]["cleaner:1"]
        self.assertEqual(cleaner["delivery"]["status"], "delivered")

    def test_recover_dispatches_the_original_pending_assignment_once(self):
        run = self.start()
        result = self.result()
        self.report(run, "coder", 1, "done", result, interrupt="call", exit_code=-signal.SIGKILL)
        before = self.assert_readonly_resume(run)
        self.assertIn("receipt", before["assignments"]["coder:1"])
        pending = before["assignments"]["cleaner:1"]
        self.assertEqual(pending["delivery"]["status"], "pending")
        self.assertEqual(before["summary"],
                         f"第 1 轮，Coder 已交付 {self.baseline[:7]}：等待向 Cleaner 派工。")
        self.assertEqual(self.attempt_count(pending), 0)
        self.assert_worker_targets(CODER)

        self.cli(run, "recover")
        delivered = self.assert_readonly_resume(run)["assignments"]["cleaner:1"]
        self.assertEqual(pending["id"], delivered["id"])
        self.assertEqual(delivered["delivery"]["status"], "delivered")
        self.report(run, "coder", 1, "done", result)
        self.cli(run, "recover")
        self.assertEqual(self.assignment_count(run, "cleaner"), 1)
        self.assertEqual(self.attempt_count(delivered), 1)
        self.assert_worker_targets(CODER, CLEANER)

    def test_recover_keeps_an_unknown_delivery_until_the_cleaner_receipt(self):
        run = self.start()
        self.report(run, "coder", 1, "done", self.result(), mode="crash", exit_code=-signal.SIGKILL)
        before = self.assert_readonly_resume(run)
        intent = before["assignments"]["cleaner:1"]
        self.assertEqual(intent["delivery"]["status"], "intent")
        self.assertEqual(before["summary"],
                         f"第 1 轮，Coder 已交付 {self.baseline[:7]}：给 Cleaner 的派工送达待核实。")
        self.assertEqual(self.attempt_count(intent), 1)
        self.assert_worker_targets(CODER, CLEANER)

        self.cli(run, "recover", exit_code=1)
        self.cli(run, "recover", exit_code=1)
        after = self.assert_readonly_resume(run)
        self.assertEqual(after["assignments"]["cleaner:1"]["delivery"]["status"], "ambiguous")
        self.assertEqual(after["summary"], before["summary"])
        self.assert_worker_targets(CODER, CLEANER)
        self.report(run, "cleaner", 1, "pass", self.result("cleaner", "pass"))
        complete = self.assert_readonly_resume(run)
        self.assertEqual(complete["phase"], "completed")
        received = complete["assignments"]["cleaner:1"]
        self.assertEqual(received["delivery"]["status"], "received")
        self.assertEqual(received["attempts"][0]["status"], "ambiguous")
        self.assertEqual(self.attempt_count(intent), 1)
        self.assert_worker_targets(CODER, CLEANER)

    def test_inspected_cleaner_delivery_is_not_a_completion_receipt(self):
        run = self.start()
        commit = self.commit("Coder delivery for inspection")
        self.report(run, "coder", 1, "done", self.result(), mode="crash", exit_code=-signal.SIGKILL)
        self.assert_worker_targets(CODER, CLEANER)
        self.cli(run, "reconcile", "cleaner", 1, 1, "delivered", "--evidence",
                 "The original Cleaner assignment is in the isolated transport log")
        state = self.assert_readonly_resume(run)
        cleaner = state["assignments"]["cleaner:1"]
        self.assertEqual(cleaner["delivery"]["status"], "delivered-by-inspection")
        self.assertNotIn("receipt", cleaner)
        self.assertEqual(self.attempt_count(cleaner), 1)
        self.assert_worker_targets(CODER, CLEANER)
        self.assertEqual(state["summary"],
                         f"第 1 轮，Coder 已交付 {commit[:7]}：已向 Cleaner 派工，尚无 Cleaner 完成回执。")

    def test_recover_does_not_replay_a_durably_delivered_assignment(self):
        run = self.start()
        self.report(run, "coder", 1, "done", self.result(), interrupt="return", exit_code=-signal.SIGKILL)
        before = self.assert_readonly_resume(run)
        cleaner = before["assignments"]["cleaner:1"]
        self.assertEqual(cleaner["delivery"]["status"], "delivered")
        self.assertNotIn("receipt", cleaner)
        self.assertEqual(self.attempt_count(cleaner), 1)
        self.assert_worker_targets(CODER, CLEANER)

        self.cli(run, "recover")
        after = self.assert_readonly_resume(run)
        self.assertEqual(after, before)
        self.assertEqual(self.attempt_count(cleaner), 1)
        self.assert_worker_targets(CODER, CLEANER)

    def test_wrong_version_missing_or_failed_coder_evidence_keeps_the_stage(self):
        run = self.start()
        commit = self.commit("Candidate delivery")
        valid = self.result_data("coder", "done", self.baseline, "candidate")
        unit = valid["checks"][0]
        rejected = {
            "wrong HEAD": dict(valid, commit=self.baseline),
            "wrong input": dict(valid, input_commit=commit),
            "missing check": dict(valid, checks=[]),
            "wrong check commit": dict(valid, checks=[dict(unit, commit=self.baseline)]),
            "wrong check command": dict(valid, checks=[dict(unit, command="true")]),
            "failed check": dict(valid, checks=[dict(unit, exit_code=1)]),
            "missing evidence": dict(valid, checks=[dict(unit, log="fixtures/absent.log")]),
        }
        for reason, evidence in rejected.items():
            with self.subTest(reason=reason):
                self.report(run, "coder", 1, "done", self.json_file("rejection.json", evidence), exit_code=1)
                state = self.assert_readonly_resume(run)
                self.assertEqual(state["current"], "coder:1")
                self.assertNotIn("receipt", state["assignments"]["coder:1"])
                self.assertEqual(self.assignment_count(run, "cleaner"), 0)
                self.assert_worker_targets(CODER)

        result = self.json_file("candidate.json", valid)
        tracked = self.project / "tracked.txt"
        original = tracked.read_bytes()
        tracked.write_text("Uncommitted\n", encoding="utf-8")
        self.report(run, "coder", 1, "done", result, exit_code=1)
        tracked.write_bytes(original)
        self.git("checkout", "--quiet", "-b", "other")
        self.report(run, "coder", 1, "done", result, exit_code=1)
        self.git("checkout", "--quiet", "delivery")
        self.assertEqual(self.assignment_count(run, "cleaner"), 0)
        self.assert_worker_targets(CODER)
        state = self.assert_readonly_resume(run)
        self.assertEqual(len(state["assignments"]["coder:1"]["rejections"]), len(rejected) + 2)
        self.report(run, "coder", 1, "done", result)
        self.assert_worker_targets(CODER, CLEANER)

    def test_cleaner_requires_a_replayable_mutant_and_its_failing_assertion(self):
        run = self.start()
        self.report(run, "coder", 1, "done", self.result())
        valid = self.result_data("cleaner", "pass", self.baseline, "cleaner")
        unit, mutation = valid["checks"]
        absent_patch = {key: value for key, value in mutation.items() if key != "patch"}
        invalid = {
            "missing patch": absent_patch,
            "pass log presented as failure": dict(mutation, log=unit["log"]),
            "inapplicable patch": dict(mutation, patch=self.evidence("inapplicable.patch", "Malformed diff\n")),
            "different assertion": dict(mutation, assertion="An unrelated assertion"),
        }
        for reason, mutant in invalid.items():
            with self.subTest(reason=reason):
                data = dict(valid, checks=[unit, mutant])
                self.report(run, "cleaner", 1, "pass", self.json_file("invalid-mutant.json", data), exit_code=1)
                state = self.assert_readonly_resume(run)
                self.assertEqual(state["phase"], "active")
                self.assertEqual(state["current"], "cleaner:1")
                self.assertNotIn("receipt", state["assignments"]["cleaner:1"])
        self.report(run, "cleaner", 1, "pass", self.json_file("valid-mutant.json", valid))
        self.assertEqual(self.assert_readonly_resume(run)["phase"], "completed")
        self.assert_worker_targets(CODER, CLEANER)

    def test_paused_receipt_stays_on_current_assignment_without_dispatching(self):
        run = self.start()
        result = self.result("coder", "paused")
        self.report(run, "coder", 1, "paused", result)
        paused = self.assert_readonly_resume(run)
        self.assertEqual(paused["phase"], "paused")
        self.assertEqual(paused["current"], "coder:1")
        self.assertEqual(paused["summary"], "第 1 轮，Coder 已暂停。")
        self.assertEqual(self.assignment_count(run, "cleaner"), 0)
        receipt = paused["assignments"]["coder:1"]["receipt"]
        self.assertIn("clarification", receipt["result"]["failure"]["reason"])
        self.cli(run, "recover")
        self.assertEqual(self.assert_readonly_resume(run), paused)
        self.assert_worker_targets(CODER)

    def test_rework_reopen_keeps_failed_and_passing_commits_and_evidence(self):
        run = self.start("--rounds", 2)
        failed_commit = self.commit("Incorrect")
        self.report(run, "coder", 1, "done", self.result())
        failure = self.result("cleaner", "fail", input_commit=failed_commit)
        self.report(run, "cleaner", 1, "fail", failure)
        after_failure = self.assert_readonly_resume(run)
        self.assertEqual(after_failure["current"], "coder:2")
        self.assertEqual(after_failure["summary"], "第 2 轮，Cleaner 验收未通过，已向 Coder 派工，尚无 Coder 完成回执。")
        self.assertEqual(after_failure["assignments"]["coder:2"]["input_commit"], failed_commit)
        self.assertIn("Misreported progress", self.worker_sends()[-1][1])

        fixed_commit = self.commit("Correct")
        self.report(run, "coder", 2, "done", self.result(input_commit=failed_commit, prefix="coder-rework"))
        self.report(run, "cleaner", 2, "pass", self.result("cleaner", "pass", input_commit=fixed_commit))
        complete = self.assert_readonly_resume(run)
        self.assertEqual(complete["phase"], "completed")
        failed = complete["assignments"]["cleaner:1"]["receipt"]
        passed = complete["assignments"]["cleaner:2"]["receipt"]
        self.assertEqual(complete["summary"],
                         f"第 2 轮，Cleaner 已在 {fixed_commit[:7]} 验收通过，two-pack 已完成。")
        self.assertEqual((failed["status"], failed["result"]["commit"]), ("fail", failed_commit))
        self.assertEqual((passed["status"], passed["result"]["commit"]), ("pass", fixed_commit))
        for receipt, expected in ((failed, "... FAIL"), (passed, "... ok")):
            check = receipt["result"]["checks"][0]
            snapshot = self.store / check["log_snapshot"]
            original = self.store / check["log"]
            original.unlink()
            self.assertIn(expected, snapshot.read_text(encoding="utf-8"))
        self.assertEqual(self.assert_readonly_resume(run), complete)
        self.assert_worker_targets(CODER, CLEANER, CODER, CLEANER)

    def test_concurrent_initialization_shares_one_project_database(self):
        argv = self.argv("init", "--project", self.project, "--card", self.card, "--checks", self.checks,
                         "--coder", CODER, "--cleaner", CLEANER)
        env = dict(self.env, HC_TERMINAL_ID=LEADER, GIT_DIR=str(self.root / "unrelated"),
                   GIT_WORK_TREE=str(self.repository))
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=env) as first:
            second = subprocess.run(argv, capture_output=True, text=True, timeout=10, env=env)
            stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        ids = [json.loads(output)["run"] for output in (stdout, second.stdout)]
        self.assertEqual(self.rows("SELECT id FROM runs ORDER BY id"), [(id_,) for id_ in sorted(ids)])
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.database.stat().st_mode & 0o077, 0)
        self.assertEqual(self.resume(ids[0])["project_id"], self.resume(ids[1])["project_id"])

    def test_pending_recovery_waits_for_the_recorded_branch_commit_and_clean_worktree(self):
        run = self.start()
        result = self.result()
        self.report(run, "coder", 1, "done", result, interrupt="call", exit_code=-signal.SIGKILL)
        pending = self.resume(run)["assignments"]["cleaner:1"]

        self.git("checkout", "--quiet", "-b", "different-branch")
        self.assertIn("recorded project, branch and input commit", self.cli(run, "recover", exit_code=1))
        self.git("checkout", "--quiet", "delivery")
        dirty = self.project / "scratch.txt"
        dirty.write_text("Uncommitted\n", encoding="utf-8")
        self.assertIn("worktree must be clean", self.cli(run, "recover", exit_code=1))
        dirty.unlink()
        self.git("commit", "--quiet", "--allow-empty", "-m", "Unexpected commit")
        self.assertIn("recorded project, branch and input commit", self.cli(run, "recover", exit_code=1))
        self.assertEqual(self.resume(run)["assignments"]["cleaner:1"], pending)
        self.assert_worker_targets(CODER)

        self.git("reset", "--quiet", "--keep", self.baseline)
        self.cli(run, "recover")
        self.assertEqual(self.attempt_count(pending), 1)
        self.assert_worker_targets(CODER, CLEANER)

    def test_reconcile_unknown_assignment_preserves_the_original_dispatch(self):
        run = self.start()
        before = self.resume(run)
        result = self.cli(run, "reconcile", "cleaner", 1, 1, "delivered", "--evidence", "Test inspection",
                          exit_code=1)
        self.assertIn("unknown assignment", result)
        self.assertEqual(self.resume(run), before)
        self.assert_worker_targets(CODER)

    def test_approved_card_revision_restarts_work_with_new_checks_and_keeps_prior_evidence(self):
        run = self.start("--rounds", 2)
        self.report(run, "coder", 1, "paused", self.result("coder", "paused"))
        card = self.file("revision.md", "# Target\nClarified criteria\n")
        revised_checks = dict(CHECKS, coder=[dict(CHECKS["coder"][0], command=f"{CODER_COMMAND} -b")])
        checks_file = self.json_file("revised-checks.json", revised_checks)

        self.cli(run, "revise", "--card", card, "--checks", checks_file)
        staged = self.resume(run)
        self.assertEqual(staged["phase"], "paused")
        self.assertEqual(staged["card_version"], 1)
        self.assertEqual(staged["card_versions"][1]["checks"], revised_checks)
        self.assertIsNone(staged["card_versions"][1]["approval"])
        self.assert_worker_targets(CODER)

        approval = "Requester approved the clarified criteria"
        self.cli(run, "revise", "--card", card, "--approve-evidence", approval)
        active = self.resume(run)
        self.assertEqual(active["current"], "coder:2")
        self.assertEqual(active["checks"], revised_checks)
        self.assertEqual(active["card"]["approval"], approval)
        self.assertEqual(active["assignments"]["coder:1"], staged["assignments"]["coder:1"])
        self.assertEqual(active["assignments"]["coder:2"]["card_version"], 2)
        self.assertIn(revised_checks["coder"][0]["command"], self.worker_sends()[-1][1])
        self.assert_worker_targets(CODER, CODER)


if __name__ == "__main__":
    unittest.main()
