"""Revision lifecycle through the six-pack CLI, with isolated dispatch and test approvals."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import signal
import socket
import unittest

import test_six_pack as six


class PlanRevisionTest(unittest.TestCase):
    def setUp(self):
        self.pack = six.SixPackTest()
        self.pack.setUp()
        self.addCleanup(self.pack.doCleanups)
        self.plan = {
            "schema": 1, "approved_examples_check": "behavior", "hardening_check": "compat",
            "qa_command": self.pack.qa_command,
            "checks": [dict(id=name, owner=owner, use="final", criterion="fixture check",
                            scope=["example"], argv=["true"], protocol="exit", fail_codes=[1])
                       for name, owner in (("behavior", "coder"), ("compat", "hardener"))],
        }

    def plan_file(self, plan):
        return self.pack.json_file(self.pack.root / "plan.json", plan)

    def init(self, plan, *extra, code=0):
        args = [arg for role, worker in six.WORKERS.items() for arg in (f"--{role}", worker)]
        return self.pack.cli("init", "--project", self.pack.project, "--card", self.pack.card,
                             "--examples", six.SPEC, "--plan", self.plan_file(plan), *extra, *args, code=code)

    def revise(self, plan, number, *, kind="tool", code=0, **options):
        flags = ("--approval-evidence", "project owner approved this fixture change") if kind == "policy" else ()
        return self.pack.cli(self.run_id, "plan-revise", "--plan", self.plan_file(plan), "--from", number,
                             "--reason", "replace fixture command", "--kind", kind, *flags, code=code, **options)

    def test_init_rejects_ambiguous_modes_invalid_plans_and_missing_fields(self):
        self.init(self.plan, "--checks", self.pack.checks, code=2)
        for field, value in (("schema", True), ("qa_command", " "), ("qa_command", "''"),
                             ("qa_command", "unterminated '"), ("approved_examples_check", "compat"),
                             ("hardening_check", "unknown")):
            with self.subTest(field=field):
                self.init({**self.plan, field: value}, code=1)
        for changes in ({"id": "user-entry"}, {"id": "behavior"}, {"owner": "lead"}, {"argv": []},
                        {"scope": []}, {"use": "observe"}, {"protocol": "other"}, {"timeout_seconds": True},
                        {"fail_codes": [True]}, {"fail_codes": [0]}, {"fail_codes": [256]},
                        {"protocol": "result"}, {"criterion": " "}):
            with self.subTest(changes=changes):
                bad = deepcopy(self.plan)
                bad["checks"][1].update(changes)
                self.init(bad, code=1)
        self.assertFalse(self.pack.database.exists())

    def test_ready_and_waiting_revisions_preserve_approval_gate_and_replay(self):
        self.run_id = self.init(self.plan)["id"]
        self.plan["checks"][0]["argv"] = ["true", "two words"]
        self.revise(self.plan, 1, caller=six.WORKERS["coder"], code=1)
        first = self.revise(self.plan, 1)
        self.assertEqual((first["phase"], first["plan_version"]), ("ready", 2))
        self.pack.cli(self.run_id, "start")
        self.assertEqual(self.pack.resume(self.run_id)["assignments"]["specifier:1"]["plan_version"], 2)
        self.pack.write_spec()
        self.pack.submit_spec(self.run_id, 1, self.pack.baseline)
        spec = self.pack.resume(self.run_id)["specification"]
        changed = deepcopy(self.plan)
        changed["checks"][0]["criterion"] = "both approved examples agree"
        self.revise(changed, 2, code=1)
        self.revise({**changed, "qa_command": "another qa"}, 2, kind="policy", code=1)
        decision = self.revise(changed, 2, kind="policy")
        self.assertEqual(decision["phase"], "waiting-for-human")
        self.assertTrue(self.revise(self.plan, 1)["duplicate"])
        self.assertEqual(self.pack.resume(self.run_id)["specification"], spec)
        self.assertIsNone(spec["approval"])
        self.assertEqual(self.pack.assignment_targets(), [six.WORKERS["specifier"]])
        self.pack.approve(self.run_id, spec)
        self.assertEqual(self.pack.resume(self.run_id)["assignments"]["coder:1"]["plan_version"], 3)
        self.pack.cli(self.run_id, "revise", "--card", self.pack.card, "--checks", self.pack.checks, code=1)

    def test_paused_specifier_remains_specifier_and_changed_examples_need_approval(self):
        self.run_id = self.init(self.plan)["id"]
        self.pack.cli(self.run_id, "start")
        self.pack.report(self.run_id, "specifier", 1, "paused",
                         dict(input_commit=self.pack.baseline, commit=self.pack.baseline, issues="",
                              checks=[], failure={"reason": "waiting for inputs"}))
        self.plan["checks"][0]["argv"] = ["true", "new"]
        decision = self.revise(self.plan, 1)
        self.assertEqual(decision["next"], "specifier:2")
        self.pack.write_spec()
        self.pack.submit_spec(self.run_id, 2, self.pack.baseline)
        spec = self.pack.resume(self.run_id)["specification"]
        self.pack.approve(self.run_id, spec)
        self.pack.report(self.run_id, "coder", 2, "blocked",
                         dict(input_commit=spec["commit"], commit=spec["commit"], issues="", checks=[],
                              specification=self.pack.specification_id(spec),
                              quality={"plan_version": 2, "executions": []}, failure={"reason": "review change"}))
        self.pack.write_spec(dict(six.QA_STEP, stdout="4\n"))
        self.plan["checks"][1]["argv"] = ["true", "replacement"]
        self.assertIn("specification", self.revise(self.plan, 2, code=1))
        self.pack.cli(self.run_id, "rework", "--specification")
        view = self.pack.resume(self.run_id)
        self.assertEqual(view["current"], "specifier:3")
        self.assertEqual(view["quality"]["plan_version"], 2)
        self.pack.submit_spec(self.run_id, 3, self.pack.head())
        self.assertIsNone(self.pack.resume(self.run_id)["specification"]["approval"])

    def submitted(self):
        self.run_id = self.init(self.plan)["id"]
        self.pack.cli(self.run_id, "start")
        self.pack.write_spec()
        self.pack.submit_spec(self.run_id, 1, self.pack.baseline)
        return self.pack.resume(self.run_id)["specification"]

    def test_plan_activation_waits_for_live_dispatch_even_after_worker_pauses(self):
        spec = self.submitted()
        gate = str(self.pack.root / "delivery.sock")
        self.pack.env.update(FAKE_MODE="hold", FAKE_GATE=gate)
        with ThreadPoolExecutor(max_workers=1) as pool, socket.socket(socket.AF_UNIX) as server:
            server.bind(gate)
            server.listen(1)
            server.settimeout(10)
            original = pool.submit(self.pack.approve, self.run_id, spec)
            connection, _ = server.accept()
            with connection:
                try:
                    self.pack.report(self.run_id, "coder", 1, "paused", dict(
                        input_commit=spec["commit"], commit=spec["commit"],
                        issues="", checks=[], specification=self.pack.specification_id(spec),
                        quality={"plan_version": 1, "executions": []}, failure={"reason": "change tool"}))
                    self.plan["checks"][0]["timeout_seconds"] = 30
                    self.assertIn("delivery", self.revise(self.plan, 1, code=1))
                    self.assertEqual(self.pack.resume(self.run_id)["quality"]["plan_version"], 1)
                finally:
                    connection.sendall(b"!")
            original.result()
        self.pack.env["FAKE_MODE"] = "ok"
        self.assertEqual(self.revise(self.plan, 1)["next"], "coder:2")

    def test_first_late_receipt_is_rejected_after_retirement_and_new_plan(self):
        old = self.submitted()
        self.pack.env["FAKE_MODE"] = "crash"
        self.pack.approve(self.run_id, old, code=-signal.SIGKILL)
        self.pack.env["FAKE_MODE"] = "ok"
        replacement = self.pack.write_spec(dict(six.QA_STEP, stdout="4\n"))
        self.pack.cli(self.run_id, "reconcile", "coder", 1, 1, "retry", "--evidence",
                      "Fixture confirmed nondelivery while the specification was changing")
        self.pack.submit_spec(self.run_id, 2, replacement)
        self.plan["checks"][0]["timeout_seconds"] = 10
        self.revise(self.plan, 1)
        new = self.pack.resume(self.run_id)["specification"]
        self.pack.approve(self.run_id, new)
        late = self.pack.report(self.run_id, "coder", 1, "paused", dict(
            input_commit=old["commit"], commit=old["commit"], issues="", checks=[],
            specification=self.pack.specification_id(old), quality={"plan_version": 1, "executions": []},
            failure={"reason": "late worker"}), code=1)
        self.assertIn("stale", late["rejection"])
        view = self.pack.resume(self.run_id)
        self.assertNotIn("receipt", view["assignments"]["coder:1"])
        self.assertEqual((view["current"], view["assignments"]["coder:2"]["plan_version"]), ("coder:2", 2))

    def test_worker_execution_confirms_uncertain_dispatch_arrived(self):
        spec = self.submitted()
        self.pack.env["FAKE_MODE"] = "crash"
        self.pack.approve(self.run_id, spec, code=-signal.SIGKILL)
        self.pack.env["FAKE_MODE"] = "ok"
        self.pack.cli(self.run_id, "check", "coder", 1, "behavior", "--key", "once", caller=six.WORKERS["coder"])
        send_count = len(self.pack.worker_sends())
        result = self.pack.cli(self.run_id, "reconcile", "coder", 1, 1, "retry", "--evidence",
                               "uncertain message", code=1)
        self.assertIn("invoked", result)
        self.pack.cli(self.run_id, "reconcile", "coder", 1, 1, "delivered", "--evidence",
                      "The assigned worker ran the check")
        self.assertEqual(len(self.pack.worker_sends()), send_count)

    def test_worktree_change_during_check_invalidates_successful_exit(self):
        self.plan["checks"][0]["argv"] = ["touch", "untracked-output"]
        spec = self.submitted()
        self.pack.approve(self.run_id, spec)
        row = self.pack.cli(self.run_id, "check", "coder", 1, "behavior", "--key", "dirty",
                            caller=six.WORKERS["coder"])
        self.assertEqual(row["outcome"], "inconclusive")
        (self.pack.project / "untracked-output").unlink()
        result = self.pack.report(self.run_id, "coder", 1, "done", dict(
            input_commit=spec["commit"], commit=spec["commit"], issues="", checks=[],
            specification=self.pack.specification_id(spec),
            quality={"plan_version": 1, "executions": [row["execution_id"]]}), code=1)
        self.assertIn("uncommitted", result["rejection"])
        self.assertEqual(self.pack.resume(self.run_id)["current"], "coder:1")

    def test_qa_revision_at_changed_head_restarts_engineering(self):
        spec = self.submitted()
        self.pack.approve(self.run_id, spec)
        data = dict(input_commit=spec["commit"], commit=spec["commit"], checks=[], issues="",
                    specification=self.pack.specification_id(spec))
        for role in ("coder", "cleaner", "architect", "hardener", "qa"):
            payload = dict(data, quality={"plan_version": 1, "executions": []})
            if role in ("coder", "hardener"):
                name = self.plan["approved_examples_check" if role == "coder" else "hardening_check"]
                row = self.pack.cli(self.run_id, "check", role, 1, name, "--key", "baseline", caller=six.WORKERS[role])
                payload["quality"]["executions"].append(row["execution_id"])
            if role == "architect":
                payload["architecture"] = "Architect reviewed the test fixture's public entry and component boundary."
            if role == "qa":
                self.pack.report(self.run_id, role, 1, "paused", dict(payload, failure={"reason": "triage"}))
            else:
                self.pack.report(self.run_id, role, 1, "done", payload)
        self.pack.write(self.pack.project / "changed", "different candidate")
        candidate = self.pack.commit("Change candidate after ordinary QA paused")
        self.plan["checks"].append(dict(self.plan["checks"][0], id="extra", owner="qa", use="stage"))
        self.assertEqual(self.revise(self.plan, 1, kind="policy")["next"], "coder:2")
        view = self.pack.resume(self.run_id)
        self.assertEqual(view["assignments"]["coder:2"]["input_commit"], candidate)
        self.assertEqual(view["specification"]["sha256"], spec["sha256"])
