"""Six-pack plan acceptance through the normal CLI with simulated hc delivery."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import test_six_pack as legacy
from test_two_pack import FAKE_HC, SCRIPT


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "quality-reader"
SPEC = "behavior/examples.json"
MANIFEST = "fixtures/v1/manifest.json"
PUBLIC = "./ci/read-current fixtures/v2/current.txt"
QA_STEP = dict(command=PUBLIC, stdout="420\n", stderr="", exit_code=0)
EXAMPLES = dict(examples=[dict(file="fixtures/v2/current.txt", cents=420),
                         dict(file="fixtures/v2/second.txt", cents=75)], qa_steps=[QA_STEP])
QA_SCRIPT = legacy.QA_SCRIPT.replace('Path("examples.json")', f'Path("{SPEC}")')
LEAD, WORKERS = legacy.LEAD, legacy.WORKERS


def definition(name, owner, use, scope, criterion, argv, protocol="result", **options):
    return dict(id=name, owner=owner, use=use, scope=scope, criterion=criterion, argv=argv,
                protocol=protocol, **options)


class QualityPlanTest(unittest.TestCase):
    write = legacy.SixPackTest.write
    json_file = legacy.SixPackTest.json_file
    git = legacy.SixPackTest.git
    commit = legacy.SixPackTest.commit
    head = legacy.SixPackTest.head
    resume = legacy.SixPackTest.resume
    worker_sends = legacy.SixPackTest.worker_sends
    assignment_targets = legacy.SixPackTest.assignment_targets
    report = legacy.SixPackTest.report
    approve = legacy.SixPackTest.approve
    specification_id = legacy.SixPackTest.specification_id
    check_result = legacy.SixPackTest.check_result
    run_check = legacy.SixPackTest.run_check
    replay_qa = legacy.SixPackTest.replay_qa
    active = legacy.SixPackTest.active
    store_contents = legacy.SixPackTest.store_contents

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="quality reader ")
        self.addCleanup(scratch.cleanup)
        self.prepare_reader(Path(scratch.name))

    def prepare_reader(self, root):
        if not shutil.which("rustc"):
            self.skipTest("the quality reader fixture requires rustc")
        self.root = root.resolve()
        self.project = self.root / "reader worktree"
        shutil.copytree(FIXTURE, self.project)
        (self.project / "behavior").mkdir(exist_ok=True)
        self.store = self.root / "private store"
        self.send_log = self.root / "transport.jsonl"
        transport = self.write(self.root / "hc", "#!/usr/bin/env python3\n" + FAKE_HC)
        transport.chmod(0o700)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}",
                        FAKE_SEND_LOG=str(self.send_log), FAKE_TARGET=WORKERS["coder"], FAKE_MODE="ok",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        PYTHONDONTWRITEBYTECODE="1", RUST_BACKTRACE="0")
        self.git("init", "--quiet")
        self.git("config", "user.name", "Quality Reader Test")
        self.git("config", "user.email", "quality-reader@example.invalid")
        self.baseline = self.commit("Reader and historical files")
        self.card = self.write(self.root / "card.md", "# Read ledger amounts\nPreserve current and historical cents.\n")
        self.script = self.store / "user-entry.py"
        self.qa_command = f"python3 -B {shlex.quote(str(self.script))}"
        self.plan = dict(schema=1, approved_examples_check="behavior", hardening_check="old-format",
                         qa_command=self.qa_command, checks=[
            definition("behavior", "coder", "final", ["reader", SPEC],
                       "Read every approved example and compare the amount", ["./ci/check", "behavior"],
                       "exit", fail_codes=[1]),
            definition("risk", "cleaner", "observe", ["reader"],
                       "Report the fixed CRAP fixture threshold and missing coverage",
                       ["./ci/check", "risk"]),
            definition("architecture", "architect", "stage", ["reader"],
                       "Require a tracked reader library separate from the public CLI",
                       ["git", "ls-files", "--error-unmatch", "reader/lib.rs"], "exit", fail_codes=[1]),
            definition("old-format", "hardener", "final", ["reader", "fixtures/v1"],
                       "Read both old binary formats and compare the amounts with the historical manifest",
                       ["./ci/check", "compat", MANIFEST]),
        ])

    def argv(self, *args):
        return [sys.executable, "-B", str(SCRIPT), str(self.store), "six", *map(str, args)]

    def cli(self, *args, caller=LEAD, code=0):
        result = subprocess.run(self.argv(*args), env=dict(self.env, HC_TERMINAL_ID=caller),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout else result.stderr.strip()

    def init(self, plan=None, **kwargs):
        workers = [arg for role, worker in WORKERS.items() for arg in (f"--{role}", worker)]
        path = self.json_file(self.root / "plan.json", self.plan if plan is None else plan)
        return self.cli("init", "--project", self.project, "--card", self.card, "--plan", path,
                        "--examples", SPEC, *workers, **kwargs)

    def waiting(self, plan=None):
        run = self.init(plan)["id"]
        source = self.head()
        self.cli(run, "start")
        target = self.project / SPEC
        previous = target.read_bytes() if target.exists() else None
        self.json_file(target, EXAMPLES)
        if target.read_bytes() != previous:
            self.commit("Specify the approved files and public entry command")
        self.report(run, "specifier", 1, "done",
                    dict(input_commit=source, commit=self.head(), issues="", checks=[], examples=SPEC))
        return run, self.resume(run)["specification"]

    def approved(self, plan=None):
        run, spec = self.waiting(plan)
        self.approve(run, spec)
        return run, self.resume(run)["specification"]

    def check(self, run, role, number, name, *, key=None, outcome="pass", code=0):
        response = self.cli(run, "check", role, number, name, "--key", key or name,
                            caller=WORKERS[role], code=code)
        if code:
            return response
        self.assertEqual((response["check"], response["outcome"]), (name, outcome), response)
        return response["execution_id"]

    def observe(self, run, role, number, check, *, key=None, outcome="pass", code=0):
        path = self.json_file(self.root / "observation.json", check)
        response = self.cli(run, "check", role, number, "--observe", path, "--key", key or check["id"],
                            caller=WORKERS[role], code=code)
        if code:
            return response
        self.assertEqual((response["check"], response["outcome"]), (check["id"], outcome), response)
        return response["execution_id"]

    def payload(self, spec, source, ids=(), *, plan_version=1, **extra):
        payload = dict(input_commit=source, commit=self.head(), specification=self.specification_id(spec),
                       issues="", checks=[], quality=dict(plan_version=plan_version, executions=list(ids)))
        payload.update(extra)
        return payload

    def send_done(self, run, spec, role, number, source, ids=(), *, plan_version=1, code=0):
        extra = {"architecture": "File decoding is separated from argument parsing."} if role == "architect" else {}
        return self.report(run, role, number, "done",
                           self.payload(spec, source, ids, plan_version=plan_version, **extra), code=code)

    def through_coder(self, run, spec, number, source):
        self.active(run, "coder", number, source)
        behavior = self.check(run, "coder", number, "behavior")
        self.send_done(run, spec, "coder", number, source, [behavior])
        return behavior

    def through_hardener(self, run, spec, number, source, *, stop=False):
        behavior = self.through_coder(run, spec, number, source)
        current = self.head()
        self.active(run, "cleaner", number, current)
        self.send_done(run, spec, "cleaner", number, current)
        self.active(run, "architect", number, current)
        stage = self.check(run, "architect", number, "architecture")
        self.send_done(run, spec, "architect", number, current, [stage])
        self.active(run, "hardener", number, current)
        if stop:
            return behavior, stage
        hardening = self.check(run, "hardener", number, "old-format")
        self.send_done(run, spec, "hardener", number, current, [hardening])
        self.active(run, "qa", number, current)
        return behavior, stage, hardening

    def qa_result(self, spec, source, ids=(), *, passing=True, plan_version=1, script=QA_SCRIPT):
        self.write(self.script, script)
        current = self.head()
        check = self.check_result("qa", "user-entry", self.qa_command, current, 0 if passing else 1)
        observations = json.loads((self.store / check["log"]).read_text(encoding="utf-8"))["observations"]
        return self.payload(spec, source, ids, plan_version=plan_version, checks=[check],
                            qa=dict(script=self.script.name, observations=observations))

    def execution(self, run, execution_id):
        return next(row for row in self.resume(run)["quality"]["executions"] if row["id"] == execution_id)

    def archived(self, row, filename):
        evidence = row["evidence"]
        artifact = next(item for item in evidence["artifacts"] if item["source"] == filename)
        data = (self.store / artifact["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
        return data

    def replace(self, path, before, after, message):
        file = self.project / path
        text = file.read_text(encoding="utf-8")
        self.assertEqual(text.count(before), 1)
        self.write(file, text.replace(before, after))
        return self.commit(message)

    def break_entry(self):
        return self.replace("reader/main.rs", 'println!("{}", amount);', 'println!("{}", amount - 1);',
                            "Add a one-cent presentation regression")

    def fix_entry(self):
        return self.replace("reader/main.rs", 'println!("{}", amount - 1);', 'println!("{}", amount);',
                            "Fix the public presentation defect")

    def break_old_format(self):
        return self.replace("reader/lib.rs", "cents.checked_add(fraction)", "cents.checked_add(fraction + 1)",
                            "Introduce a v1 decimal amount regression")

    def fix_old_format(self):
        return self.replace("reader/lib.rs", "cents.checked_add(fraction + 1)", "cents.checked_add(fraction)",
                            "Preserve the historical decimal amount")

    def assert_rejected(self, result):
        self.assertIsInstance(result, dict)
        self.assertTrue(result["rejection"], result)

    def assert_complete(self, run, commit, expected):
        view = self.resume(run)
        self.assertEqual((view["phase"], view["final_commit"]), ("completed", commit))
        self.assertEqual(view["quality"]["final"]["executions"], expected)
        self.assertEqual(self.git("status", "--porcelain"), "")
        return view

    def pause(self, run, spec, role, number, source, *, plan_version=1):
        payload = self.payload(spec, source, plan_version=plan_version,
                               failure=dict(reason="Wait for the authorized quality plan change."))
        self.report(run, role, number, "paused", payload)
        self.assertEqual(self.resume(run)["phase"], "paused")
        return payload

    def revise_args(self, run, base, plan, *, kind="tool", reason="Equivalent project tool invocation",
                    evidence=None, filename="revision.json"):
        path = self.json_file(self.root / filename, plan)
        args = [run, "plan-revise", "--from", str(base), "--plan", path, "--kind", kind, "--reason", reason]
        if evidence is not None:
            args.extend(("--approval-evidence", evidence))
        return args

    def test_a2_rust_reader_compatibility_trial(self):
        self.run_rust_reader_trial()

    def run_rust_reader_trial(self):
        run, spec = self.approved()
        behavior, stage, compat = self.through_hardener(run, spec, 1, spec["commit"])
        result = self.qa_result(spec, self.head(), [behavior, compat])
        self.report(run, "qa", 1, "pass", result)
        view = self.assert_complete(run, self.head(), {"behavior": behavior, "old-format": compat})
        self.assertEqual(self.assignment_targets(), list(WORKERS.values()))
        receipt = view["assignments"]["hardener:1"]["receipt"]["result"]
        self.assertEqual(receipt["quality"]["executions"], [compat])
        self.assertEqual(receipt["checks"], [])
        run_row = self.execution(run, compat)
        self.assertEqual((run_row["commit"], run_row["plan_version"], run_row["spec_sha256"]),
                         (self.head(), 1, spec["sha256"]))
        native = json.loads(self.archived(run_row, "native-report.json"))
        self.assertEqual([(Path(row["file"]).name, row["expected_cents"], row["actual_cents"]) for row in native],
                         [("v1-integer.bin", 275, 275), ("v1-decimal.bin", 1234, 1234)])
        self.assertIn(b"rustc", self.archived(run_row, "build.log"))
        stdout = self.execution(run, behavior)["evidence"]["stdout"]
        behavior_log = (self.store / stdout["path"]).read_text(encoding="utf-8")
        self.assertIn("fixtures/v2/current.txt", behavior_log)
        self.assertIn("fixtures/v2/second.txt", behavior_log)
        self.assertIn("All 2 samples decoded", behavior_log)
        self.assertEqual(self.execution(run, stage)["use"], "stage")
        self.assertEqual(view["assignments"]["qa:1"]["receipt"]["result"]["qa"]["observations"], [QA_STEP])
        return view

    def test_a3_amount_mismatch_returns_hardener_to_coder(self):
        run, spec = self.approved()
        self.break_old_format()
        broken = self.head()
        self.through_hardener(run, spec, 1, spec["commit"], stop=True)
        compat = self.check(run, "hardener", 1, "old-format", outcome="fail")
        self.assert_rejected(self.send_done(run, spec, "hardener", 1, broken, [compat], code=1))
        self.active(run, "hardener", 1, broken)
        native = json.loads(self.archived(self.execution(run, compat), "native-report.json"))
        self.assertEqual((native[1]["expected_cents"], native[1]["actual_cents"]), (1234, 1235))
        diagnosis = dict(check="old-format", input="v1-decimal.bin", expected="1234", actual="1235")
        self.report(run, "hardener", 1, "fail", self.payload(spec, broken, [compat], failure=diagnosis))
        self.active(run, "coder", 2, broken)
        self.assertIn("1235", self.worker_sends()[-1][1])
        fixed = self.fix_old_format()
        behavior, _, compat = self.through_hardener(run, spec, 2, broken)
        self.report(run, "qa", 2, "pass", self.qa_result(spec, fixed))
        self.assert_complete(run, fixed, {"behavior": behavior, "old-format": compat})

    def test_a3_empty_missing_and_unbuildable_compatibility_are_inconclusive(self):
        for condition, detail in (("empty", "both v1 samples"), ("missing", "failed to execute"),
                                  ("build failure", "native Rust build failed")):
            with self.subTest(condition=condition):
                run, spec = self.approved()
                self.through_hardener(run, spec, 1, spec["commit"], stop=True)
                if condition == "empty":
                    self.json_file(self.project / MANIFEST, [])
                elif condition == "missing":
                    (self.project / "fixtures/v1/v1-decimal.bin").unlink()
                else:
                    self.write(self.project / "reader/native_probe.rs", "fn main() { missing_function(); }\n")
                self.commit(f"Make compatibility {condition}")
                execution = self.check(run, "hardener", 1, "old-format", outcome="inconclusive")
                self.assertIn(detail, self.execution(run, execution)["summary"])
                self.assert_rejected(self.send_done(run, spec, "hardener", 1, spec["commit"], [execution], code=1))
                failure = dict(check="old-format", input="v1 archive", expected="1234", actual="unknown")
                self.assert_rejected(self.report(run, "hardener", 1, "fail",
                                                 self.payload(spec, spec["commit"], [execution], failure=failure),
                                                 code=1))
                self.active(run, "hardener", 1, spec["commit"])
                self.git("reset", "--hard", spec["commit"])

    def test_a3_python_and_rust_targeted_mutation(self):
        modes = {"target": "pass", "compile-error": "inconclusive", "zero-tests": "inconclusive",
                 "wrong-assertion": "inconclusive", "survives": "fail"}
        plan = deepcopy(self.plan)
        plan["checks"].pop()
        for language in ("python", "rust"):
            for mode in modes:
                name = f"{language}-{mode}"
                plan["checks"].append(definition(name, "hardener", "stage" if mode == "target" else "observe",
                                                 ["reader"], "Prove the exact test catches the fraction regression",
                                                 ["./ci/check", "mutation", language, mode]))
        plan["hardening_check"] = "rust-target"
        run, spec = self.approved(plan)
        behavior, _ = self.through_hardener(run, spec, 1, spec["commit"], stop=True)
        accepted = []
        for language in ("python", "rust"):
            for mode, outcome in modes.items():
                with self.subTest(language=language, mode=mode):
                    name = f"{language}-{mode}"
                    execution = self.check(run, "hardener", 1, name, outcome=outcome)
                    row = self.execution(run, execution)
                    self.assertIn(b"... ok", self.archived(row, "original.log"))
                    patch = self.archived(row, "mutation.patch")
                    self.assertIn(b"+++ b/reader/", patch)
                    if mode == "target":
                        accepted.append(execution)
                        replay = subprocess.run(["git", "apply", "--check", "-"], cwd=self.project, env=self.env,
                                                input=patch, capture_output=True, timeout=5)
                        self.assertEqual(replay.returncode, 0, replay.stderr)
                        log = self.archived(row, "mutant.log")
                        self.assertIn(b"decimal fraction is preserved", log)
                        self.assertIn(b"1200", log)
                        self.assertIn(b"1234", log)
                    if mode == "zero-tests":
                        count = b"running 0 tests" if language == "rust" else b"Ran 0 tests"
                        self.assertIn(count, self.archived(row, "mutant.log"))
        self.send_done(run, spec, "hardener", 1, self.head(), accepted)
        self.report(run, "qa", 1, "pass", self.qa_result(spec, self.head()))
        self.assert_complete(run, self.head(), {"behavior": behavior})

    def test_a4_crap_observation_and_authorized_gate(self):
        risk_path = self.project / "fixtures/risk.json"
        score = json.loads(risk_path.read_text(encoding="utf-8"))
        for observation in ("untested", "inconclusive", "fail"):
            with self.subTest(observation=observation):
                run, spec = self.approved()
                if observation == "inconclusive":
                    self.json_file(risk_path, dict(score, crap=None))
                    self.commit("Use the fixed risk fixture without coverage")
                elif observation == "fail":
                    self.json_file(risk_path, score)
                    self.commit("Use the fixed risk fixture over its threshold")
                self.through_coder(run, spec, 1, spec["commit"])
                ids = []
                if observation != "untested":
                    ids.append(self.check(run, "cleaner", 1, "risk", outcome=observation))
                    row = self.execution(run, ids[0])
                    self.assertEqual(json.loads(self.archived(row, "crap-report.json"))["source"], "fixed test fixture")
                view = self.resume(run)
                self.assertEqual(view["assignments"]["cleaner:1"]["quality_checks"]["risk"]["outcome"], observation)
                self.send_done(run, spec, "cleaner", 1, self.head(), ids)
                self.active(run, "architect", 1, self.head())

        self.pause(run, spec, "architect", 1, self.head())
        plan = deepcopy(self.plan)
        plan["checks"][1]["use"] = "stage"
        revision = self.revise_args(run, 1, plan, kind="policy", reason="Require the fixed coverage threshold",
                                    evidence="Fixture authorization from project owner for the stage gate")
        self.cli(*revision)
        bad = self.head()
        self.active(run, "cleaner", 2, bad)
        failed = self.check(run, "cleaner", 2, "risk", outcome="fail")
        self.assert_rejected(self.send_done(run, spec, "cleaner", 2, bad, [failed], plan_version=2, code=1))
        self.json_file(risk_path, dict(score, crap=None))
        self.commit("Remove coverage data from the fixed report")
        unknown = self.check(run, "cleaner", 2, "risk", key="without-coverage", outcome="inconclusive")
        self.assert_rejected(self.send_done(run, spec, "cleaner", 2, bad, [unknown], plan_version=2, code=1))
        self.active(run, "cleaner", 2, bad)

    def start_qa_repair(self, run, spec, broken):
        entry = self.qa_result(spec, broken, passing=False)
        self.assertEqual(entry["qa"]["observations"], [dict(QA_STEP, stdout="419\n")])
        entry["failure"] = dict(check="user-entry", input=PUBLIC, expected="420", actual="419")
        entry["issues"] = "The CLI subtracts a cent from the amount after decoding."
        self.report(run, "qa", 1, "repair", entry)
        self.active(run, "qa", 2, broken)

    def test_a5_qa_repair_requires_the_original_script_and_current_final_checks(self):
        run, spec = self.approved()
        broken = self.break_entry()
        old_behavior, _, old_compat = self.through_hardener(run, spec, 1, spec["commit"])
        self.start_qa_repair(run, spec, broken)
        fixed = self.fix_entry()
        self.assert_rejected(self.report(run, "qa", 2, "pass", self.qa_result(spec, broken), code=1))
        self.assert_rejected(self.report(run, "qa", 2, "pass",
                                         self.qa_result(spec, broken, [old_behavior, old_compat]), code=1))
        behavior = self.check(run, "qa", 2, "behavior")
        self.assert_rejected(self.report(run, "qa", 2, "pass", self.qa_result(spec, broken, [behavior]), code=1))
        compat = self.check(run, "qa", 2, "old-format")
        changed = self.qa_result(spec, broken, [behavior, compat], script=QA_SCRIPT + "# changed after finding\n")
        self.assert_rejected(self.report(run, "qa", 2, "pass", changed, code=1))
        self.active(run, "qa", 2, broken)
        self.report(run, "qa", 2, "pass", self.qa_result(spec, broken, [behavior, compat]))
        view = self.assert_complete(run, fixed, {"behavior": behavior, "old-format": compat})
        first = view["assignments"]["qa:1"]["receipt"]
        second = view["assignments"]["qa:2"]["receipt"]
        self.assertEqual(first["result"]["qa"]["script_snapshot"], second["result"]["qa"]["script_snapshot"])
        self.replay_qa(broken, first)
        self.replay_qa(fixed, second)
        row = self.execution(run, compat)
        self.assertEqual((row["owner"], row["role"], row["commit"]), ("hardener", "qa", fixed))
        self.assertNotIn("coder:2", view["assignments"])

    def test_a6_ordinary_qa_green_entry_and_red_final_returns_to_coder(self):
        plan = deepcopy(self.plan)
        sample = "fixtures/v2/extended.txt"
        manifest = "fixtures/v2/archive.json"
        plan["checks"].append(definition("extended-archive", "qa", "final", ["reader", sample],
                                         "The extended archive contains exactly 900 cents",
                                         ["./ci/check", "archive", manifest]))
        run, spec = self.approved(plan)
        self.write(self.project / sample, "C901\n")
        self.json_file(self.project / manifest, [dict(file=Path(sample).name, cents=900)])
        broken = self.commit("Import an extended archive with the wrong amount")
        self.through_hardener(run, spec, 1, spec["commit"])
        failed = self.check(run, "qa", 1, "extended-archive", outcome="fail")
        self.assert_rejected(self.report(run, "qa", 1, "pass", self.qa_result(spec, broken, [failed]), code=1))
        qa = self.qa_result(spec, broken, [failed])
        self.assertEqual(qa["qa"]["observations"], [QA_STEP])
        diagnosis = dict(check="extended-archive", input=sample, expected="900", actual="901")
        self.assert_rejected(self.report(run, "qa", 1, "fail", dict(qa, failure=diagnosis,
                                        quality=dict(plan_version=1, executions=[])), code=1))
        self.report(run, "qa", 1, "fail", dict(qa, failure=diagnosis))
        self.assert_qa_rework(run, 1, broken, failed, diagnosis)
        fixed = self.replace(sample, "901", "900", "Correct the extended archive amount")
        behavior, _, compat = self.through_hardener(run, spec, 2, broken)
        archive = self.check(run, "qa", 2, "extended-archive")
        self.report(run, "qa", 2, "pass", self.qa_result(spec, fixed, [archive]))
        self.assert_complete(run, fixed, {"behavior": behavior, "old-format": compat, "extended-archive": archive})

    def assert_qa_rework(self, run, number, commit, failed, diagnosis):
        view = self.active(run, "coder", number + 1, commit)
        receipt = view["assignments"][f"qa:{number}"]["receipt"]
        self.assertEqual(view["assignments"][f"coder:{number + 1}"]["source_receipt"], receipt["id"])
        self.assertEqual(receipt["result"]["failure"], diagnosis)
        self.assertEqual(receipt["result"]["qa"]["observations"], [QA_STEP])
        self.assertIn(failed, receipt["result"]["quality"]["executions"])
        self.assertIn(failed, self.worker_sends()[-1][1])
        self.assertIn(diagnosis["input"], self.worker_sends()[-1][1])
        self.replay_qa(commit, receipt)

    def test_a6_repair_qa_green_entry_and_red_final_returns_to_coder(self):
        run, spec = self.approved()
        broken = self.break_entry()
        self.through_hardener(run, spec, 1, spec["commit"])
        self.start_qa_repair(run, spec, broken)
        self.fix_entry()
        repair = self.break_old_format()
        failed = self.check(run, "qa", 2, "old-format", outcome="fail")
        self.assert_rejected(self.report(run, "qa", 2, "pass", self.qa_result(spec, broken, [failed]), code=1))
        diagnosis = dict(check="old-format", input="v1-decimal.bin", expected="1234", actual="1235")
        self.report(run, "qa", 2, "fail", dict(self.qa_result(spec, broken, [failed]), failure=diagnosis))
        self.assert_qa_rework(run, 2, repair, failed, diagnosis)
        fixed = self.fix_old_format()
        behavior, _, compat = self.through_hardener(run, spec, 3, repair)
        self.report(run, "qa", 3, "pass", self.qa_result(spec, fixed))
        self.assert_complete(run, fixed, {"behavior": behavior, "old-format": compat})

    def test_a7_observation_revisions_and_idempotency(self):
        run, spec = self.approved()
        behavior = self.through_coder(run, spec, 1, spec["commit"])
        current = self.head()
        extra = definition("extra-risk", "cleaner", "observe", ["reader"], "Inspect the fixed risk report",
                           ["./ci/check", "risk"])
        before = self.assignment_targets()
        observed = self.observe(run, "cleaner", 1, extra, outcome="fail")
        self.assertEqual(self.observe(run, "cleaner", 1, extra, outcome="fail"), observed)
        self.assertIn("conflict", self.observe(run, "cleaner", 1, dict(extra, criterion="Changed"), code=1))
        self.assertEqual(self.assignment_targets(), before)
        self.assertEqual(self.resume(run)["specification"], spec)
        self.assertEqual(self.resume(run)["quality"]["plan_version"], 1)
        self.assertTrue(self.execution(run, observed)["source"].endswith("observation.json"))
        self.send_done(run, spec, "cleaner", 1, current, [observed])
        stage = self.check(run, "architect", 1, "architecture")
        self.send_done(run, spec, "architect", 1, current, [stage])
        prior = self.check(run, "hardener", 1, "old-format")
        self.assertEqual(self.check(run, "hardener", 1, "old-format"), prior)
        self.send_done(run, spec, "hardener", 1, current, [prior])
        changed_tool = deepcopy(self.plan)
        changed_tool["checks"][-1]["argv"][1] = "compat-alt"
        args = self.revise_args(run, 1, changed_tool)
        self.assertIn("paused", self.cli(*args, code=1))
        paused = self.pause(run, spec, "qa", 1, current)
        self.assertEqual(self.cli(*args)["plan_version"], 2)
        sends = self.assignment_targets()
        self.assertTrue(self.cli(*args)["duplicate"])
        self.assertTrue(self.report(run, "qa", 1, "paused", paused)["duplicate"])
        self.assert_rejected(self.report(run, "qa", 1, "paused", dict(paused, issues="late edit"), code=1))
        self.assertEqual(self.assignment_targets(), sends)
        view = self.active(run, "hardener", 2, current)
        self.assertEqual(view["specification"], spec)
        self.assertEqual(view["assignments"]["hardener:2"]["plan_version"], 2)
        self.assertEqual(self.execution(run, prior)["plan_version"], 1)
        self.assert_rejected(self.send_done(run, spec, "hardener", 2, current, [prior], plan_version=2, code=1))
        new = self.check(run, "hardener", 2, "old-format")
        previous_row, new_row = self.execution(run, prior), self.execution(run, new)
        for row in (previous_row, new_row):
            self.assertEqual((row["commit"], row["spec_sha256"]), (current, spec["sha256"]))
        self.assertNotEqual(previous_row["argv"], new_row["argv"])
        self.assertNotEqual(previous_row["tool"], new_row["tool"])
        self.assertIn(b"reader/native_probe.rs", self.archived(previous_row, "build.log"))
        self.assertIn(b"reader/main.rs", self.archived(new_row, "public-build.log"))
        native = self.archived(new_row, "public-reader.tsv")
        self.assertIn(b"fixtures/v1/v1-integer.bin\t275\t275", native)
        self.assertIn(b"fixtures/v1/v1-decimal.bin\t1234\t1234", native)
        self.send_done(run, spec, "hardener", 2, current, [new], plan_version=2)
        self.assert_rejected(self.report(run, "qa", 2, "pass",
                                         self.qa_result(spec, current, [behavior, new], plan_version=2), code=1))
        self.pause(run, spec, "qa", 2, current, plan_version=2)
        policy = deepcopy(changed_tool)
        policy["checks"][-1]["criterion"] += " Require the separate sample identifiers."
        self.assertIn("policy", self.cli(*self.revise_args(run, 2, policy), code=1))
        self.cli(*self.revise_args(run, 2, policy, kind="policy"), code=1)
        evidence = "Fixture approval to retain each archive sample identifier"
        policy_args = self.revise_args(run, 2, policy, kind="policy", evidence=evidence)
        self.assertEqual(self.cli(*policy_args)["plan_version"], 3)
        self.assertTrue(self.cli(*policy_args)["duplicate"])
        revision = self.active(run, "hardener", 3, current)["quality"]["plan_versions"][-1]
        self.assertEqual((revision["kind"], revision["approval_evidence"]), ("policy", evidence))
        self.assertIn("stale", self.cli(*self.revise_args(run, 1, changed_tool, reason="Different intent"), code=1))
        latest = self.check(run, "hardener", 3, "old-format")
        self.send_done(run, spec, "hardener", 3, current, [latest], plan_version=3)
        final_behavior = self.check(run, "qa", 3, "behavior")
        self.report(run, "qa", 3, "pass", self.qa_result(spec, current, [final_behavior], plan_version=3))
        self.assert_complete(run, current, {"behavior": final_behavior, "old-format": latest})

    def test_a8_foreign_stale_and_forged_evidence_is_rejected(self):
        foreign_run, _ = self.approved()
        foreign = self.check(foreign_run, "coder", 1, "behavior")
        run, spec = self.approved()
        source = self.head()
        original = self.check(run, "coder", 1, "behavior")
        self.assert_rejected(self.send_done(run, spec, "coder", 1, source, [foreign], code=1))
        self.assert_rejected(self.send_done(run, spec, "coder", 1, source, ["exec-99999"], code=1))
        fake_check = dict(name="behavior", command="./ci/check behavior", commit=source, exit_code=0)
        self.assert_rejected(self.report(run, "coder", 1, "done", self.payload(spec, source, checks=[fake_check]),
                                         code=1))
        self.write(self.project / "tracked-note", "New candidate; same examples.\n")
        self.commit("Commit an unrelated change to the fixture")
        self.assert_rejected(self.send_done(run, spec, "coder", 1, source, [original], code=1))
        self.assertIn("conflict", self.check(run, "coder", 1, "behavior", code=1))
        self.assertIn("terminal", self.cli(run, "check", "coder", 1, "behavior", "--key", "wrong-terminal", code=1))
        self.assertIn("cannot execute", self.check(run, "coder", 1, "old-format", code=1))
        dirty = self.write(self.project / "dirty.untracked", "A check cannot start on this worktree.\n")
        self.assertIn("uncommitted", self.check(run, "coder", 1, "behavior", key="dirty", code=1))
        dirty.unlink()
        current = self.check(run, "coder", 1, "behavior", key="new-commit")
        self.send_done(run, spec, "coder", 1, source, [current])
        self.active(run, "cleaner", 1, self.head())

    def fixture_process(self, *args, caller=LEAD, **variables):
        process = subprocess.Popen(self.argv(*args), env=dict(self.env, HC_TERMINAL_ID=caller, **variables),
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

        self.addCleanup(cleanup)
        return process

    def test_a8_interrupted_tool_is_readable_and_never_replayed(self):
        gate = self.root / "gate"
        launches = self.root / "launches"
        code = (
            "from pathlib import Path\n"
            "import socket, sys\n"
            "with Path(sys.argv[2]).open('a', encoding='utf-8') as out:\n"
            "    out.write('launched\\n')\n"
            "with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gate:\n"
            "    gate.connect(sys.argv[1])\n"
            "    gate.recv(1)\n"
        )
        plan = deepcopy(self.plan)
        plan["checks"].append(definition("gated", "coder", "observe", ["reader"], "Wait on a fixture socket",
                                         [sys.executable, "-B", "-c", code, str(gate), str(launches)],
                                         "exit", timeout_seconds=20, fail_codes=[]))
        run, spec = self.approved(plan)
        source = self.head()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(gate))
            server.listen(1)
            server.settimeout(10)
            started = self.fixture_process(run, "check", "coder", 1, "gated", "--key", "waiting",
                                           caller=WORKERS["coder"])
            conn, _ = server.accept()
            with conn:
                conn.settimeout(5)
                running = self.resume(run)["quality"]["executions"][-1]
                self.assertEqual(running["status"], "running")
                identifier = running["id"]
                self.assertEqual(self.check(run, "coder", 1, "gated", key="waiting", outcome="running"), identifier)
                started.kill()
                started.communicate(timeout=5)
                self.assertLess(started.returncode, 0)
                self.assertEqual(self.execution(run, identifier)["status"], "running")
                self.assert_rejected(self.report(run, "coder", 1, "paused",
                                                 self.payload(spec, source, failure=dict(reason="Tool unresolved")),
                                                 code=1))
                self.cli(run, "recover")
                self.assertEqual(self.execution(run, identifier)["status"], "running")
                conn.sendall(b"!")
                self.assertEqual(conn.recv(1), b"")
        deadline = time.monotonic() + 5
        while self.execution(run, identifier)["status"] == "running":
            self.assertLess(time.monotonic(), deadline, "the tool lock was not released")
            time.sleep(0.2)
        before = self.store_contents(), self.send_log.read_bytes()
        self.assertEqual(self.execution(run, identifier)["status"], "interrupted")
        self.assertEqual((self.store_contents(), self.send_log.read_bytes()), before)
        self.assertEqual(self.check(run, "coder", 1, "gated", key="waiting", outcome="inconclusive"), identifier)
        self.assertIn("interrupted", self.execution(run, identifier)["summary"])
        self.assertEqual(launches.read_text(encoding="utf-8"), "launched\n")
        self.through_coder(run, spec, 1, source)

    def test_a6_a8_qa_reexecution_of_hardener_final_uses_startup_order(self):
        gate = self.root / "gate"
        code = (
            "import os, socket, sys\n"
            "mode = os.getenv('QUALITY_TEST_MODE')\n"
            "if mode == 'hold':\n"
            "    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gate:\n"
            "        gate.connect(sys.argv[1])\n"
            "        gate.recv(1)\n"
            "raise SystemExit(1 if mode == 'fail' else 0)\n"
        )
        plan = deepcopy(self.plan)
        plan["checks"].append(definition("startup-order", "hardener", "final", ["reader"],
                                         "Exercise startup ordering with a simulated tool",
                                         [sys.executable, "-B", "-c", code, str(gate)],
                                         "exit", fail_codes=[1], timeout_seconds=20))
        run, spec = self.approved(plan)
        commit = self.head()
        self.through_hardener(run, spec, 1, spec["commit"], stop=True)
        compat = self.check(run, "hardener", 1, "old-format")
        owner = self.check(run, "hardener", 1, "startup-order")
        self.send_done(run, spec, "hardener", 1, commit, [compat, owner])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(gate))
            server.listen(1)
            server.settimeout(10)
            older = self.fixture_process(run, "check", "qa", 1, "startup-order", "--key", "older",
                                         caller=WORKERS["qa"], QUALITY_TEST_MODE="hold")
            conn, _ = server.accept()
            with conn:
                newer = self.fixture_process(run, "check", "qa", 1, "startup-order", "--key", "newer",
                                             caller=WORKERS["qa"], QUALITY_TEST_MODE="fail")
                stdout, stderr = newer.communicate(timeout=10)
                self.assertEqual(newer.returncode, 0, stderr)
                latest = json.loads(stdout)
                self.assertEqual(latest["outcome"], "fail")
                rejection = self.report(run, "qa", 1, "pass", self.qa_result(spec, commit), code=1)
                self.assertIn("has not finished", rejection["rejection"])
                conn.sendall(b"!")
        stdout, stderr = older.communicate(timeout=10)
        self.assertEqual(older.returncode, 0, stderr)
        older_result = json.loads(stdout)
        self.assertEqual(older_result["outcome"], "pass")
        for candidate in ([], [owner], [older_result["execution_id"]]):
            self.assert_rejected(self.report(run, "qa", 1, "pass", self.qa_result(spec, commit, candidate), code=1))
        newest = self.execution(run, latest["execution_id"])
        self.assertEqual((newest["owner"], newest["role"], newest["commit"], newest["outcome"]),
                         ("hardener", "qa", commit, "fail"))
        diagnosis = dict(check="startup-order", input="Simulated tool verdict", expected="0", actual="1")
        result = self.qa_result(spec, commit, [newest["id"]])
        self.report(run, "qa", 1, "fail", dict(result, failure=diagnosis))
        self.assert_qa_rework(run, 1, commit, newest["id"], diagnosis)

    def test_a8_concurrent_revisions_leave_one_current_assignment(self):
        run, spec = self.approved()
        self.pause(run, spec, "coder", 1, self.head())
        updates = []
        for timeout in (30, 35):
            plan = deepcopy(self.plan)
            plan["checks"][0]["timeout_seconds"] = timeout
            updates.append(self.revise_args(run, 1, plan, filename=f"revision-{timeout}.json"))
        processes = [self.fixture_process(*args) for args in updates]
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            outcomes.append((process.returncode, stdout, stderr))
        self.assertEqual(sorted(code for code, _, _ in outcomes), [0, 1], outcomes)
        winner = next(index for index, (code, _, _) in enumerate(outcomes) if code == 0)
        self.assertIn("stale", outcomes[1 - winner][2])
        view = self.active(run, "coder", 2, self.head())
        self.assertEqual(list(view["assignments"]), ["specifier:1", "coder:1", "coder:2"])
        self.assertEqual([item["plan_version"] for item in view["quality"]["plan_versions"]], [1, 2])
        self.assertTrue(self.cli(*updates[winner])["duplicate"])
        self.assertEqual(self.assignment_targets(), [WORKERS["specifier"], WORKERS["coder"], WORKERS["coder"]])
