"""Exercise the public helper against an isolated hc executable."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/herdr-dev-team/scripts/handoff.py"
LEADER, CODER, REVIEWER = "t1", "t2x", "t3z"
FAKE_HC = r'''
import json
import os
from pathlib import Path
import signal
import socket
import sys

command, flag, terminal, message = sys.argv[1:]
assert (command, flag) == ("send", "--json")
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps([terminal, message]) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if "receipt saved" in message:
    state = json.loads(Path(os.environ["FAKE_STATE"]).read_text())
    assert "receipt" in state["assignments"][state["current"]]
if mode == "crash":
    os.kill(os.getppid(), signal.SIGKILL)
if mode == "hold":
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gate:
        gate.connect(os.environ["FAKE_GATE"])
        gate.settimeout(5)
        assert gate.recv(1) == b"!"
if mode == "error":
    print('{"error":{"kind":"unavailable"}}', file=sys.stderr)
    sys.exit(1)
if mode == "false-ok":
    print('{"sent":false}')
else:
    print(json.dumps({"turn": 17, "started": mode != "not-started"}))
'''


class HandoffTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="handoff test ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.run_dir = self.root / "private run"
        self.log_path = self.root / "transport.jsonl"
        self.gate = self.root / "gate"
        hc = self.root / "hc"
        hc.write_text("#!/usr/bin/env python3\n" + FAKE_HC, encoding="utf-8")
        hc.chmod(0o700)
        self.config = self.file("config.txt", "Confirmed Pi Coder and Reviewer settings\n")
        self.brief = self.file("brief.txt", "Implement the change.\nCheck the final result.\n")
        self.review = self.file("review.txt", "Review the delivered change.\n")
        self.result = self.file("result.txt", "Changed the file and verified the public path.\n")
        self.env = dict(os.environ, PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}",
                        FAKE_LOG=str(self.log_path), FAKE_STATE=str(self.run_dir / "state.json"),
                        FAKE_GATE=str(self.gate))

    def file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def argv(self, *args):
        return [sys.executable, str(SCRIPT), str(self.run_dir), *map(str, args)]

    def cli(self, *args, caller=LEADER, mode="ok", exit_code=0):
        result = subprocess.run(self.argv(*args), text=True, capture_output=True, timeout=5,
                                env=dict(self.env, HC_TERMINAL_ID=caller, FAKE_MODE=mode))
        self.assertEqual(result.returncode, exit_code, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout else result.stderr.strip()

    def init(self, *extra, confirm=True, exit_code=0):
        flags = ["--confirm"] if confirm else []
        return self.cli("init", *flags, "--project", self.root, "--config", self.config,
                        "--coder", CODER, "--reviewer", REVIEWER, *extra, exit_code=exit_code)

    def start(self, **kwargs):
        return self.cli("start", "--brief", self.brief, **kwargs)

    def report(self, role="coder", number=1, status="done", path=None, **kwargs):
        kwargs.setdefault("caller", CODER if role == "coder" else REVIEWER)
        return self.cli("report", role, number, status, "--result", path or self.result, **kwargs)

    def sends(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def test_normal_handoff_and_duplicate_delivery(self):
        self.assertIn("--confirm", self.init(confirm=False, exit_code=1))
        self.assertEqual(self.init(), self.init())
        original = self.start()
        self.assertEqual(original, self.start())
        self.assertEqual(original["delivery"]["status"], "delivered")
        self.assertFalse(original["receipt_saved"])
        self.assertIn("no saved receipt", self.cli("advance", "coder", 1, "--brief", self.review, exit_code=1))
        self.report()
        saved = (self.run_dir / "state.json").read_bytes()
        self.report()
        self.assertEqual(saved, (self.run_dir / "state.json").read_bytes())
        review = self.cli("advance", "coder", 1, "--brief", self.review)
        self.assertEqual(review, self.cli("advance", "coder", 1, "--brief", self.review))
        self.assertIn("different brief", self.cli("advance", "coder", 1, "--brief", self.brief, exit_code=1))
        self.assertIn("stale", self.report(exit_code=1))
        self.report("reviewer", status="pass")
        self.assertEqual({"phase": "qa-ready"}, self.cli("advance", "reviewer", 1))
        self.assertEqual({"phase": "qa-ready"}, self.cli("advance", "reviewer", 1))
        self.assertEqual([CODER, LEADER, LEADER, REVIEWER, LEADER], [target for target, _ in self.sends()])
        self.assertIn(original["assignment"], self.sends()[0][1])
        self.assertIn("report coder 1 STATUS --result RESULT_FILE", self.sends()[0][1])

    def test_saved_receipt_survives_failed_notification_and_leader_takeover(self):
        self.init()
        self.start()
        report = self.report(mode="error", exit_code=1)
        self.assertTrue(report["receipt_saved"])
        self.assertEqual(report["notification"]["status"], "ambiguous")
        resumed = self.cli("resume", caller="t9")
        self.assertNotIn("takeover_guidance", resumed)
        receipt = resumed["assignments"]["coder:1"]["receipt"]
        self.assertEqual(receipt["result"], self.result.read_text())
        self.assertIn("recorded Leader", self.cli("advance", "coder", 1, "--brief", self.review,
                                                  caller="t9", exit_code=1))
        sends_before = self.sends()
        takeover = self.cli("takeover", "--from", LEADER, caller="t9")
        self.assertEqual(takeover["leader"], "t9")
        guidance = takeover["takeover_guidance"]
        self.assertIn("target t9", guidance["receipt_route"])
        self.assertIn("blocked, error, and exit", guidance["core_note_route"])
        self.assertIn("original parent", guidance["core_note_route"])
        self.assertEqual(guidance["inspect_once"], {
            "coder": f"hc get --json {CODER}", "reviewer": f"hc get --json {REVIEWER}",
        })
        self.assertIn("operator to alert the current Leader", guidance["action"])
        self.assertIn("resume and inspect that worker once", guidance["action"])
        self.assertEqual(self.cli("resume", caller="t9")["takeover_guidance"], guidance)
        self.assertEqual(self.cli("takeover", "--from", "t9", caller="t9"), takeover)
        self.assertEqual(self.cli("resume", caller="t9")["former_leaders"], [LEADER])
        self.assertEqual(self.sends(), sends_before)
        self.report()
        self.assertEqual(self.sends()[-1][0], "t9")
        self.cli("advance", "coder", 1, "--brief", self.review, caller="t9")
        self.assertEqual(self.sends()[-1][0], REVIEWER)

    def test_crash_after_send_is_not_replayed(self):
        self.init()
        self.start(mode="crash", exit_code=-signal.SIGKILL)
        resumed = self.cli("resume")
        item = resumed["assignments"]["coder:1"]
        self.assertEqual(item["attempts"], [{"status": "intent"}])
        self.assertEqual(item["target"], CODER)
        pending = self.start(exit_code=1)
        self.assertEqual(pending["attempt"], 1)
        self.assertEqual(len(self.sends()), 1)
        self.assertIn(item["id"], self.sends()[0][1])
        reconciled = self.cli("reconcile", "coder", 1, 1, "delivered", "--evidence", "Prompt visible in target")
        self.assertEqual(reconciled["delivery"]["status"], "delivered-by-inspection")
        self.assertEqual(len(self.sends()), 1)
        self.report()
        self.cli("advance", "coder", 1, "--brief", self.review)
        self.assertEqual([CODER, LEADER, REVIEWER], [target for target, _ in self.sends()])

    def test_receipt_can_resolve_ambiguous_delivery_without_rewriting_attempt(self):
        self.init()
        self.start(mode="error", exit_code=1)
        self.report(status="blocked")
        original = self.start()
        self.assertTrue(original["receipt_saved"])
        self.assertEqual(original["delivery"]["status"], "ambiguous")
        self.assertIn("no longer", self.cli("reconcile", "coder", 1, 1, "retry", "--evidence", "No send",
                                          exit_code=1))
        self.assertEqual({"phase": "blocked"}, self.cli("advance", "coder", 1))
        self.assertEqual([CODER, LEADER], [target for target, _ in self.sends()])

    def test_reconcile_retry_requires_current_attempt_and_keeps_target(self):
        self.init()
        pending = self.start(mode="false-ok", exit_code=1)
        self.assertEqual(pending["delivery"]["status"], "ambiguous")
        self.assertEqual(self.start(exit_code=1), pending)
        self.assertEqual(len(self.sends()), 1)
        reconcile = ("reconcile", "coder", 1, 1, "retry", "--evidence", "Transport refused before delivery")
        repaired = self.cli(*reconcile, mode="not-started")
        self.assertEqual(repaired["target"], CODER)
        self.assertEqual(repaired["attempt"], 2)
        self.assertFalse(repaired["delivery"]["hc"]["started"])
        self.assertFalse(repaired["receipt_saved"])
        self.assertIn("no saved receipt", self.cli("advance", "coder", 1, "--brief", self.review, exit_code=1))
        self.assertIn("no longer", self.cli(*reconcile, exit_code=1))
        self.assertEqual([CODER, CODER], [target for target, _ in self.sends()])
        attempts = self.cli("resume")["assignments"]["coder:1"]["attempts"]
        self.assertEqual(attempts[0]["status"], "not-delivered-by-inspection")

    def test_concurrent_advance_reserves_only_one_review_and_keeps_early_receipt(self):
        self.init()
        self.start()
        self.report()
        command = ("advance", "coder", 1, "--brief", self.review)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.gate))
            server.listen()
            server.settimeout(5)
            with subprocess.Popen(self.argv(*command), text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  env=dict(self.env, HC_TERMINAL_ID=LEADER, FAKE_MODE="hold")) as first:
                gate, _ = server.accept()
                with gate:
                    try:
                        self.assertIn("in progress", self.cli(*command, exit_code=1))
                        self.assertIn("in progress", self.cli("reconcile", "reviewer", 1, 1, "retry",
                                                              "--evidence", "No send", exit_code=1))
                        self.report("reviewer", status="pass")
                    finally:
                        gate.sendall(b"!")
                stdout, stderr = first.communicate(timeout=5)
                self.assertEqual(first.returncode, 0, stderr)
                self.assertTrue(json.loads(stdout)["receipt_saved"])
        self.cli(*command)
        self.assertEqual([CODER, LEADER, REVIEWER, LEADER], [target for target, _ in self.sends()])

    def test_rejects_wrong_worker_round_status_empty_and_conflict(self):
        self.init()
        self.start()
        self.assertIn("wrong or stale", self.report(number=2, exit_code=1))
        self.assertIn("assigned worker", self.report(caller=REVIEWER, exit_code=1))
        self.assertIn("invalid coder status", self.report(status="pass", exit_code=1))
        self.assertIn("empty file", self.report(path=self.file("empty.txt", "\n"), exit_code=1))
        self.report()
        self.assertIn("conflicting receipt", self.report(status="blocked", exit_code=1))
        self.assertIn("conflicting receipt", self.report(path=self.review, exit_code=1))
        self.assertEqual([CODER, LEADER], [target for target, _ in self.sends()])
        self.assertEqual(self.cli("resume")["assignments"]["coder:1"]["receipt"]["status"], "done")

    def test_repairs_reuse_terminals_and_budget_is_enforced(self):
        self.init("--rounds", 2)
        self.start()
        self.report()
        self.cli("advance", "coder", 1, "--brief", self.review)
        self.report("reviewer", status="fail")
        self.cli("advance", "reviewer", 1, "--brief", self.brief)
        self.report(number=2)
        self.cli("advance", "coder", 2, "--brief", self.review)
        self.report("reviewer", number=2, status="fail")
        self.assertIn("review-exhausted", self.cli("advance", "reviewer", 2, "--brief", self.brief, exit_code=1))
        self.assertEqual({"phase": "review-exhausted"}, self.cli("advance", "reviewer", 2))
        self.assertEqual([CODER, REVIEWER, CODER, REVIEWER], [t for t, _ in self.sends() if t != LEADER])


if __name__ == "__main__":
    unittest.main()
