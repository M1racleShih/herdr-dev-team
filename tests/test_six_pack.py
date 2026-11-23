"""Exercise the six-role handoff with real checks and a private QA script."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from test_two_pack import FAKE_HC, SCRIPT


LEAD = "t11"
WORKERS = dict(specifier="t22", coder="t33", cleaner="t44", architect="t55", hardener="t66", qa="t77")
SPEC = "examples.json"
UNIT = "python3 -m unittest -v test_increment.UnitTests"
ACCEPTANCE = "python3 -m unittest -v test_increment.BehaviorTests"
PROPERTIES = "python3 -m unittest -v test_increment.PropertyTests"
ARCHITECT_UNIT = "python3 -m unittest -v test_increment.UnitTests.test_increments_an_integer"
DEPENDENCIES = "python3 -m unittest -v test_increment.DependencyTests"
USER_COMMAND = "python3 increment.py 2"
QA_STEP = dict(command=USER_COMMAND, stdout="3\n", stderr="", exit_code=0)
TEST_CASES = '''import json
import ast
from pathlib import Path
import unittest

from increment import increment


class UnitTests(unittest.TestCase):
    def test_increments_an_integer(self):
        self.assertEqual(increment(2), 3, "increment adds exactly one")


class BehaviorTests(unittest.TestCase):
    def test_approved_examples(self):
        for case in json.loads(Path("examples.json").read_text())["examples"]:
            self.assertEqual(increment(case["input"]), case["expected"])


class PropertyTests(unittest.TestCase):
    def test_preserves_integer_spacing(self):
        for number in range(-5, 6):
            self.assertEqual(increment(number + 1) - increment(number), 1)


class DependencyTests(unittest.TestCase):
    def test_only_cli_dependency_is_sys(self):
        imports = [node.names[0].name for node in ast.walk(ast.parse(Path("increment.py").read_text()))
                   if isinstance(node, ast.Import)]
        self.assertEqual(imports, ["sys"])
'''
QA_SCRIPT = '''import hashlib
import json
from pathlib import Path
import shlex
import subprocess


steps = json.loads(Path("examples.json").read_text())["qa_steps"]
observations = []
for step in steps:
    result = subprocess.run(shlex.split(step["command"]), capture_output=True, text=True, timeout=5)
    observations.append(dict(command=step["command"], stdout=result.stdout, stderr=result.stderr,
                             exit_code=result.returncode))
code = int(steps != observations)
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
print(json.dumps(dict(commit=commit, script_sha256=digest, observations=observations, exit_code=code)))
raise SystemExit(code)
'''


class SixPackTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="six-pack test ")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.project = self.root / "target"
        self.project.mkdir()
        self.store = self.root / "private store"
        self.database = self.store / "two-pack.sqlite3"
        self.send_log = self.root / "transport.jsonl"
        transport = self.write(self.root / "hc", "#!/usr/bin/env python3\n" + FAKE_HC)
        transport.chmod(0o700)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}",
                        FAKE_SEND_LOG=str(self.send_log), FAKE_TARGET=WORKERS["coder"], FAKE_MODE="ok",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "--quiet")
        self.git("config", "user.name", "Six Pack Test")
        self.git("config", "user.email", "six-pack@example.invalid")
        self.write_target("number", print_result="number")
        self.write(self.project / "test_increment.py", TEST_CASES)
        self.baseline = self.commit("Baseline")
        self.card = self.write(self.root / "card.md", "# Increment\nIncrement an integer from the terminal.\n")
        self.script = self.store / "qa.py"
        self.qa_command = f"python3 -B {shlex.quote(str(self.script))}"
        behavior = [dict(name="unit", command=UNIT, exit_code=0),
                    dict(name="acceptance", command=ACCEPTANCE, exit_code=0)]
        self.pins = {
            "specifier": [],
            "coder": behavior,
            "cleaner": behavior,
            "architect": [*behavior, dict(name="properties", command=PROPERTIES, exit_code=0)],
            "hardener": [*behavior, dict(name="mutation", command=UNIT, exit_code=1)],
            "qa": [dict(name="user-entry", command=self.qa_command, exit_code=0)],
        }
        self.checks = self.json_file(self.root / "checks.json", self.pins)

    def write(self, path, content):
        path.write_text(content, encoding="utf-8")
        return path

    def json_file(self, path, value):
        return self.write(path, json.dumps(value) + "\n")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *map(str, args)], cwd=cwd or self.project, env=self.env,
                              check=True, capture_output=True, text=True, timeout=10).stdout

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.head()

    def head(self, cwd=None):
        return self.git("rev-parse", "HEAD", cwd=cwd).strip()

    def write_target(self, increment_result, *, print_result):
        self.write(self.project / "increment.py", f'''import sys


def increment(number):
    return {increment_result}


if __name__ == "__main__":
    number = int(sys.argv[1])
    print({print_result})
''')

    def write_spec(self, step=QA_STEP):
        self.json_file(self.project / SPEC, dict(examples=[dict(input=2, expected=3)], qa_steps=[step]))
        return self.commit("Specify increment behavior")

    def cli(self, *args, caller=LEAD, code=0, pack="six"):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.store), pack, *map(str, args)],
                                env=dict(self.env, HC_TERMINAL_ID=caller), text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        return json.loads(result.stdout) if result.stdout else result.stderr.strip()

    def init(self, **kwargs):
        workers = [arg for role, worker in WORKERS.items() for arg in (f"--{role}", worker)]
        return self.cli("init", "--project", self.project, "--card", self.card, "--checks", self.checks,
                        "--examples", SPEC, *workers, **kwargs)

    def resume(self, run):
        return self.cli(run, "resume", caller="")

    def worker_sends(self):
        if not self.send_log.exists():
            return []
        sends = map(json.loads, self.send_log.read_text(encoding="utf-8").splitlines())
        return [(target, message) for target, message in sends if target in WORKERS.values()]

    def assignment_targets(self):
        return [target for target, _ in self.worker_sends()]

    def store_contents(self):
        return {path.relative_to(self.store): path.read_bytes() for path in self.store.rglob("*") if path.is_file()}

    def report(self, run, role, number, status, payload, **kwargs):
        path = self.json_file(self.root / f"{role}-{number}-{status}.json", payload)
        return self.cli(run, "report", role, number, status, "--result", path,
                        caller=WORKERS[role], **kwargs)

    def submit_spec(self, run, number, source, **kwargs):
        return self.report(run, "specifier", number, "done",
                           dict(input_commit=source, commit=self.head(), issues="", checks=[], examples=SPEC),
                           **kwargs)

    def waiting(self):
        run = self.init()["id"]
        self.cli(run, "start")
        self.write_spec()
        self.submit_spec(run, 1, self.baseline)
        return run, self.resume(run)["specification"]

    def approve(self, run, spec, **kwargs):
        return self.cli(run, "approve", spec["version"], spec["sha256"], "--evidence",
                        "Human fixture approval of these exact examples and QA outputs", **kwargs)

    def specification_id(self, spec):
        return {key: spec[key] for key in ("version", "sha256")}

    def run_check(self, command, *, cwd=None):
        return subprocess.run(shlex.split(command), cwd=cwd or self.project, env=self.env,
                              text=True, capture_output=True, check=False, timeout=10)

    def check_result(self, role, name, command, commit, code=0, *, cwd=None):
        process = self.run_check(command, cwd=cwd)
        output = process.stdout + process.stderr
        self.assertEqual(process.returncode, code, output)
        log = self.write(self.store / f"{role}-{commit}-{name}.log", output)
        return dict(name=name, command=command, commit=commit, exit_code=process.returncode, log=log.name)

    def checks_for(self, role, source, spec):
        commit = self.head()
        checks = [self.check_result(role, check["name"], check["command"], commit, check["exit_code"])
                  for check in self.pins[role] if check["name"] != "mutation"]
        return dict(input_commit=source, commit=commit, issues="", checks=checks,
                    specification=self.specification_id(spec))

    def mutation_result(self, commit):
        mutant = self.root / f"mutant-{commit}"
        self.git("clone", "--quiet", self.project, mutant, cwd=self.root)
        self.assertEqual(self.head(mutant), commit)
        target = mutant / "increment.py"
        code = target.read_text(encoding="utf-8")
        self.assertEqual(code.count("return number + 1"), 1)
        self.write(target, code.replace("return number + 1", "return number + 2"))
        patch = self.write(self.store / f"hardener-{commit}.patch", self.git("diff", cwd=mutant))
        result = self.check_result("hardener", "mutation", UNIT, commit, 1, cwd=mutant)
        return dict(result, patch=patch.name, test="test_increment.UnitTests.test_increments_an_integer",
                    assertion="increment adds exactly one")

    def qa_result(self, commit, spec, *, stdout, passing, script=QA_SCRIPT):
        self.write(self.script, script)
        check = self.check_result("qa", "user-entry", self.qa_command, commit, 0 if passing else 1)
        log = json.loads((self.store / check["log"]).read_text(encoding="utf-8"))
        observations = [dict(QA_STEP, stdout=stdout)]
        self.assertEqual(log["observations"], observations)
        result = dict(input_commit=commit, commit=commit, issues="", checks=[check],
                      specification=self.specification_id(spec),
                      qa=dict(script=self.script.name, observations=observations))
        if not passing:
            result["failure"] = dict(check="user-entry", input=USER_COMMAND, expected="3", actual="2")
        return result

    def diagnosed_qa_failure(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        self.write_target("number + 1", print_result="number")
        bad = self.commit("Implement with a broken entry point")
        self.advance_to_qa(run, spec, 1, spec["commit"], bad)
        failure = self.qa_result(bad, spec, stdout="2\n", passing=False)
        failure["issues"] = "The CLI prints its input instead of the computed value."
        return run, spec, bad, failure

    def start_local_qa_repair(self):
        run, spec, bad, failure = self.diagnosed_qa_failure()
        self.report(run, "qa", 1, "repair", failure)
        return run, spec, bad

    def pin_repair_checks_with_dependencies(self):
        self.pins["architect"] = [
            dict(name="unit", command=ARCHITECT_UNIT, exit_code=0),
            *[check for check in self.pins["architect"] if check["name"] != "unit"],
            dict(name="dependencies", command=DEPENDENCIES, exit_code=0),
        ]
        self.json_file(self.checks, self.pins)

    def repair_checks(self, commit):
        pins = [*self.pins["coder"],
                *[check for check in self.pins["architect"] if check["name"] in ("properties", "dependencies")]]
        return [self.check_result("qa", check["name"], check["command"], commit, check["exit_code"])
                for check in pins]

    def repair_result(self, source, spec):
        commit = self.head()
        result = self.qa_result(commit, spec, stdout="3\n", passing=True)
        result["input_commit"] = source
        result["checks"].extend(self.repair_checks(commit))
        return result

    def claimed_repair_pass(self, source, spec):
        """Build otherwise-complete passing evidence to isolate commit validation."""
        commit = self.head()
        result = self.qa_result(commit, spec, stdout="2\n", passing=False)
        result["input_commit"] = source
        del result["failure"]
        result["qa"]["observations"] = [QA_STEP]
        check = result["checks"][0]
        check["exit_code"] = 0
        check["log"] = self.json_file(
            self.store / f"claimed-pass-{commit}.json",
            dict(commit=commit, script_sha256=hashlib.sha256(self.script.read_bytes()).hexdigest(),
                 observations=[QA_STEP], exit_code=0),
        ).name
        result["checks"].extend(self.repair_checks(commit))
        return result

    def replay_qa(self, commit, receipt):
        clone = self.root / f"replay-{commit}"
        self.git("clone", "--quiet", self.project, clone, cwd=self.root)
        self.git("checkout", "--quiet", "--detach", commit, cwd=clone)
        result = receipt["result"]
        script = self.store / result["qa"]["script_snapshot"]
        process = self.run_check(f"python3 -B {shlex.quote(str(script))}", cwd=clone)
        log = json.loads((self.store / result["checks"][0]["log_snapshot"]).read_text(encoding="utf-8"))
        self.assertEqual(process.returncode, log["exit_code"])
        self.assertEqual(json.loads(process.stdout), log)
        self.assertEqual(log["observations"], result["qa"]["observations"])

    def active(self, run, role, number, commit):
        view = self.resume(run)
        assignment = f"{role}:{number}"
        self.assertEqual(view["phase"], "active")
        self.assertEqual(view["current"], assignment)
        self.assertEqual(view["assignments"][assignment]["input_commit"], commit)
        self.assertNotIn("final_commit", view)
        return view

    def advance_to_qa(self, run, spec, number, source, commit):
        self.active(run, "coder", number, source)
        self.report(run, "coder", number, "done", self.checks_for("coder", source, spec))
        self.active(run, "cleaner", number, commit)
        self.report(run, "cleaner", number, "done", self.checks_for("cleaner", commit, spec))
        self.active(run, "architect", number, commit)
        architecture = self.checks_for("architect", commit, spec)
        architecture["architecture"] = "The computation is callable independently of CLI argument parsing."
        self.report(run, "architect", number, "done", architecture)
        self.active(run, "hardener", number, commit)
        hardening = self.checks_for("hardener", commit, spec)
        hardening["checks"].append(self.mutation_result(commit))
        self.report(run, "hardener", number, "done", hardening)
        return self.active(run, "qa", number, commit)

    def test_human_approval_and_resume_are_durable(self):
        card = "# 加一\n目标\u3000输入 2 应显示 3。\n"
        self.write(self.card, card)
        run, spec = self.waiting()
        before = self.store_contents(), self.send_log.read_bytes()
        view = self.resume(run)
        self.assertEqual(view["card"]["body"], card)
        self.assertEqual(view["pack"], "six")
        self.assertEqual(view["phase"], "waiting-for-human")
        self.assertEqual(list(view["assignments"]), ["specifier:1"])
        body = (self.project / SPEC).read_bytes()
        self.assertEqual((spec["commit"], spec["path"], spec["body"], spec["sha256"]),
                         (self.head(), SPEC, body.decode(), hashlib.sha256(body).hexdigest()))
        self.assertIsNone(spec["approval"])
        self.assertIn(self.database.relative_to(self.store), before[0])
        self.assertEqual((self.store_contents(), self.send_log.read_bytes()), before)
        self.cli(run, "start")
        self.cli(run, "recover")
        self.approve(run, spec, caller=WORKERS["specifier"], code=1)
        self.approve(run, dict(spec, sha256="0" * 64), code=1)
        self.approve(run, dict(spec, version=spec["version"] + 1), code=1)
        self.assertEqual(self.assignment_targets(), [WORKERS["specifier"]])
        self.approve(run, spec)
        view = self.active(run, "coder", 1, spec["commit"])
        self.assertEqual(view["assignments"]["coder:1"]["specification"], self.specification_id(spec))
        self.assertIn("Human fixture approval", view["specification"]["approval"]["evidence"])
        self.approve(run, spec)
        self.cli(run, "recover")
        self.assertEqual(self.resume(run), view)
        self.assertEqual(self.assignment_targets(), [WORKERS["specifier"], WORKERS["coder"]])

    def test_qa_fails_green_checks_then_accepts_repaired_commit(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        self.write_target("number + 1", print_result="number")
        bad = self.commit("Implement the increment function")
        first_qa = self.advance_to_qa(run, spec, 1, spec["commit"], bad)
        failed = self.qa_result(bad, spec, stdout="2\n", passing=False)
        self.report(run, "qa", 1, "fail", failed)
        rework = self.active(run, "coder", 2, bad)
        self.assertEqual(rework["specification"], first_qa["specification"])
        self.assertEqual(rework["assignments"]["qa:1"]["receipt"]["status"], "fail")
        self.assertEqual(rework["assignments"]["coder:2"]["source_receipt"],
                         rework["assignments"]["qa:1"]["receipt"]["id"])
        coder_message = self.worker_sends()[-1][1]
        self.assertIn("Prior role receipt (fail)", coder_message)
        self.assertIn("Observed QA mismatch", coder_message)
        finding = coder_message.split("Observed QA mismatch", 1)[1]
        self.assertIn(USER_COMMAND, finding)
        self.assertIn("3", finding)
        self.assertIn("2", finding)

        no_fix = self.report(run, "coder", 2, "done", self.checks_for("coder", bad, spec), code=1)
        self.assertIn("new commit", no_fix["rejection"])
        self.active(run, "coder", 2, bad)

        self.write_target("number + 1", print_result="increment(number)")
        fixed = self.commit("Use the increment result at the public entry point")
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), bad)
        self.advance_to_qa(run, spec, 2, bad, fixed)
        changed_script = self.qa_result(fixed, spec, stdout="3\n", passing=True,
                                        script=QA_SCRIPT + "# Changed after the QA failure\n")
        rejected = self.report(run, "qa", 2, "pass", changed_script, code=1)
        self.assertIn("script", rejected["rejection"])
        self.active(run, "qa", 2, fixed)
        accepted = self.qa_result(fixed, spec, stdout="3\n", passing=True)
        self.report(run, "qa", 2, "pass", accepted)

        view = self.resume(run)
        self.assertEqual(view["phase"], "completed")
        self.assertEqual(view["final_commit"], fixed)
        self.assertEqual(self.assignment_targets(), list(WORKERS.values()) + list(WORKERS.values())[1:])
        for number, commit, stdout, status, expected_code in (
            (1, bad, "2\n", "fail", 1),
            (2, fixed, "3\n", "pass", 0),
        ):
            with self.subTest(round=number):
                receipt = view["assignments"][f"qa:{number}"]["receipt"]
                self.assertEqual(receipt["status"], status)
                self.assertEqual(receipt["result"]["commit"], commit)
                qa = receipt["result"]["qa"]
                self.assertEqual(qa["observations"], [dict(QA_STEP, stdout=stdout)])
                check = receipt["result"]["checks"][0]
                log = json.loads((self.store / check["log_snapshot"]).read_text(encoding="utf-8"))
                script = self.store / qa["script_snapshot"]
                self.assertEqual(script.read_text(encoding="utf-8"), QA_SCRIPT)
                self.assertEqual(log, dict(commit=commit, script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                                           observations=qa["observations"], exit_code=expected_code))
                hardener = view["assignments"][f"hardener:{number}"]["receipt"]["result"]
                mutation = next(check for check in hardener["checks"] if check["name"] == "mutation")
                patch = (self.store / mutation["patch_snapshot"]).read_text(encoding="utf-8")
                self.assertIn("+    return number + 2", patch)
                mutation_log = (self.store / mutation["log_snapshot"]).read_text(encoding="utf-8")
                self.assertIn("AssertionError: 4 != 3 : increment adds exactly one", mutation_log)

        self.assertEqual(self.git("status", "--porcelain"), "")
        self.script.unlink()
        (self.store / accepted["checks"][0]["log"]).unlink()
        before = self.store_contents(), self.send_log.read_bytes()
        self.assertTrue(self.report(run, "qa", 2, "pass", accepted)["duplicate"])
        self.cli(run, "recover")
        self.assertEqual(self.resume(run), view)
        self.assertEqual((self.store_contents(), self.send_log.read_bytes()), before)

    def test_qa_local_repair_requires_new_commit_and_frozen_checks(self):
        self.pin_repair_checks_with_dependencies()
        run, spec, bad, failed = self.diagnosed_qa_failure()
        invalid = {
            "missing diagnosis": dict(failed, issues=""),
            "missing failure": {key: value for key, value in failed.items() if key != "failure"},
            "missing failure detail": dict(failed, failure=dict(failed["failure"], actual="")),
            "missing QA observations": {key: value for key, value in failed.items() if key != "qa"},
            "missing user-entry check": dict(failed, checks=[]),
            "wrong observation": dict(failed, qa=dict(failed["qa"], observations=[QA_STEP])),
        }
        matching = deepcopy(failed)
        matching["qa"]["observations"] = [QA_STEP]
        matching["checks"][0]["log"] = self.json_file(
            self.store / "matching-observations.json",
            dict(commit=bad, script_sha256=hashlib.sha256(self.script.read_bytes()).hexdigest(),
                 observations=[QA_STEP], exit_code=1),
        ).name
        invalid["no mismatch in approved observations"] = matching
        for label, result in invalid.items():
            with self.subTest(invalid_repair=label):
                self.assertIn("rejection", self.report(run, "qa", 1, "repair", result, code=1))
                self.active(run, "qa", 1, bad)

        self.report(run, "qa", 1, "repair", failed)
        view = self.active(run, "qa", 2, bad)
        self.assertEqual(view["specification"], dict(spec, approval=view["specification"]["approval"]))
        self.assertEqual(view["assignments"]["qa:1"]["receipt"]["status"], "repair")
        self.assertEqual(view["assignments"]["qa:2"]["source_receipt"],
                         view["assignments"]["qa:1"]["receipt"]["id"])
        self.assertNotIn("coder:2", view["assignments"])
        self.assertEqual(self.assignment_targets(), list(WORKERS.values()) + [WORKERS["qa"]])
        message = self.worker_sends()[-1][1]
        self.assertIn("Local QA repair checks:", message)
        for command in (UNIT, ACCEPTANCE, PROPERTIES, DEPENDENCIES):
            self.assertIn(command, message)
        self.assertNotIn(ARCHITECT_UNIT, message)
        self.assertTrue(self.report(run, "qa", 1, "repair", failed)["duplicate"])
        self.cli(run, "recover")
        self.assertEqual(self.resume(run), view)
        self.assertEqual(self.assignment_targets().count(WORKERS["qa"]), 2)
        self.assertIn("rejection", self.report(run, "qa", 2, "repair", failed, code=1))
        self.active(run, "qa", 2, bad)

        no_change = self.claimed_repair_pass(bad, spec)
        rejected = self.report(run, "qa", 2, "pass", no_change, code=1)
        self.assertIn("commit", rejected["rejection"])
        self.active(run, "qa", 2, bad)

        # A new, unrelated commit is not a descendant of the assigned candidate.
        tree = self.git("rev-parse", f"{bad}^{{tree}}").strip()
        unrelated = self.git("commit-tree", tree, "-p", spec["commit"], "-m", "Unrelated candidate").strip()
        self.git("reset", "--hard", unrelated)
        rejected = self.report(run, "qa", 2, "pass", self.claimed_repair_pass(bad, spec), code=1)
        self.assertIn("descend", rejected["rejection"])
        self.git("reset", "--hard", bad)

        self.git("commit", "--quiet", "--allow-empty", "-m", "No actual repair")
        empty = self.head()
        rejected = self.report(run, "qa", 2, "pass", self.claimed_repair_pass(bad, spec), code=1)
        self.assertIn("commit", rejected["rejection"])
        self.active(run, "qa", 2, bad)

        self.write_target("number + 1", print_result="increment(number)")
        fixed = self.commit("Repair the public entry point")
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), empty)
        valid = self.repair_result(bad, spec)
        alternatives = {}
        for name in ("user-entry", "unit", "acceptance", "properties", "dependencies"):
            alternatives[f"missing {name}"] = dict(valid, checks=[row for row in valid["checks"]
                                                                    if row["name"] != name])
        for name in ("unit", "acceptance", "properties", "dependencies"):
            stale = deepcopy(valid)
            next(row for row in stale["checks"] if row["name"] == name)["commit"] = bad
            alternatives[f"stale {name} commit"] = stale
        wrong_unit = deepcopy(valid)
        unit = next(row for row in wrong_unit["checks"] if row["name"] == "unit")
        unit.update(self.check_result("qa", "unit-architect", ARCHITECT_UNIT, fixed))
        unit["name"] = "unit"
        alternatives["Architect unit in place of frozen Coder unit"] = wrong_unit
        stale_log = deepcopy(valid)
        stale_log["checks"][0]["log"] = failed["checks"][0]["log"]
        alternatives["stale user-entry log"] = stale_log
        wrong_input = dict(valid, input_commit=spec["commit"])
        alternatives["stale assignment input"] = wrong_input
        for label, result in alternatives.items():
            with self.subTest(invalid_pass=label):
                self.assertIn("rejection", self.report(run, "qa", 2, "pass", result, code=1))
                self.active(run, "qa", 2, bad)

        changed = self.qa_result(fixed, spec, stdout="3\n", passing=True,
                                 script=QA_SCRIPT + "# Changed after the QA finding\n")
        changed["input_commit"] = bad
        changed["checks"].extend(self.repair_checks(fixed))
        rejected = self.report(run, "qa", 2, "pass", changed, code=1)
        self.assertIn("script", rejected["rejection"])
        self.active(run, "qa", 2, bad)
        valid = self.repair_result(bad, spec)
        self.report(run, "qa", 2, "pass", valid)

        view = self.resume(run)
        self.assertEqual(view["phase"], "completed")
        self.assertEqual(view["final_commit"], fixed)
        self.assertNotIn("coder:2", view["assignments"])
        for number, commit, status, stdout in ((1, bad, "repair", "2\n"), (2, fixed, "pass", "3\n")):
            with self.subTest(round=number):
                receipt = view["assignments"][f"qa:{number}"]["receipt"]
                self.assertEqual((receipt["status"], receipt["result"]["commit"]), (status, commit))
                self.assertEqual(receipt["result"]["qa"]["observations"], [dict(QA_STEP, stdout=stdout)])
                self.replay_qa(commit, receipt)
        self.assertEqual(view["assignments"]["qa:1"]["receipt"]["result"]["qa"]["script_snapshot"],
                         view["assignments"]["qa:2"]["receipt"]["result"]["qa"]["script_snapshot"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue(self.report(run, "qa", 2, "pass", valid)["duplicate"])
        self.cli(run, "recover")
        self.assertEqual(self.resume(run), view)

    def test_qa_repair_pass_without_an_optional_dependency_check(self):
        run, spec, bad = self.start_local_qa_repair()
        self.write_target("number + 1", print_result="increment(number)")
        fixed = self.commit("Fix the CLI result")
        result = self.repair_result(bad, spec)
        self.assertEqual({check["name"] for check in result["checks"]},
                         {"user-entry", "unit", "acceptance", "properties"})
        self.report(run, "qa", 2, "pass", result)
        view = self.resume(run)
        self.assertEqual((view["phase"], view["final_commit"]), ("completed", fixed))
        self.assertNotIn("coder:2", view["assignments"])

    def test_paused_qa_repair_preserves_original_finding_and_checks(self):
        run, spec, bad = self.start_local_qa_repair()
        self.git("commit", "--quiet", "--allow-empty", "-m", "Pause without changing the candidate")
        paused_at = self.head()
        paused = dict(input_commit=bad, commit=paused_at, issues="", checks=[],
                      specification=self.specification_id(spec), failure=dict(reason="Need more investigation."))
        self.report(run, "qa", 2, "paused", paused)
        self.cli(run, "recover")
        self.assertEqual(self.resume(run)["phase"], "paused")
        self.cli(run, "rework")
        view = self.active(run, "qa", 3, paused_at)
        self.assertEqual(view["assignments"]["qa:3"]["source_receipt"],
                         view["assignments"]["qa:2"]["receipt"]["id"])
        message = self.worker_sends()[-1][1]
        self.assertIn("QA repair diagnosis", message)
        self.assertIn("Local QA repair checks:", message)
        no_change = self.claimed_repair_pass(paused_at, spec)
        rejected = self.report(run, "qa", 3, "pass", no_change, code=1)
        self.assertIn("check-only", rejected["rejection"])

        self.write_target("number + 1", print_result="increment(number)")
        fixed = self.commit("Fix the entry point after pausing")
        self.git("merge-base", "--is-ancestor", bad, fixed)
        paused_after_fix = dict(input_commit=paused_at, commit=fixed, issues="", checks=[],
                                specification=self.specification_id(spec),
                                failure=dict(reason="Need to finish the final checks."))
        self.report(run, "qa", 3, "paused", paused_after_fix)
        self.cli(run, "rework")
        view = self.active(run, "qa", 4, fixed)
        self.assertEqual(view["assignments"]["qa:4"]["source_receipt"],
                         view["assignments"]["qa:3"]["receipt"]["id"])
        incomplete = self.repair_result(fixed, spec)
        incomplete["checks"] = [check for check in incomplete["checks"] if check["name"] != "properties"]
        rejected = self.report(run, "qa", 4, "pass", incomplete, code=1)
        self.assertIn("missing required check properties", rejected["rejection"])
        changed = self.qa_result(fixed, spec, stdout="3\n", passing=True,
                                 script=QA_SCRIPT + "# Changed after the pause\n")
        changed["input_commit"] = fixed
        changed["checks"].extend(self.repair_checks(fixed))
        rejected = self.report(run, "qa", 4, "pass", changed, code=1)
        self.assertIn("script", rejected["rejection"])
        self.report(run, "qa", 4, "pass", self.repair_result(fixed, spec))
        self.assertEqual(self.resume(run)["final_commit"], fixed)

    def test_ordinary_qa_pause_rework_verifies_any_new_commit(self):
        run, spec, bad, _ = self.diagnosed_qa_failure()
        paused = dict(input_commit=bad, commit=bad, issues="", checks=[],
                      specification=self.specification_id(spec),
                      failure=dict(reason="Need more investigation before reporting a finding."))
        self.report(run, "qa", 1, "paused", paused)
        self.cli(run, "rework")
        view = self.active(run, "qa", 2, bad)
        self.assertEqual(view["assignments"]["qa:2"]["source_receipt"],
                         view["assignments"]["qa:1"]["receipt"]["id"])

        self.report(run, "qa", 2, "paused", paused)
        self.write_target("number + 1", print_result="increment(number)")
        fixed = self.commit("Change the entry point after QA paused")
        self.cli(run, "rework")
        view = self.active(run, "coder", 3, fixed)
        self.assertEqual(view["assignments"]["coder:3"]["source_receipt"],
                         view["assignments"]["qa:2"]["receipt"]["id"])
        self.assertNotIn("qa:3", view["assignments"])
        self.advance_to_qa(run, spec, 3, fixed, fixed)
        self.report(run, "qa", 3, "pass", self.qa_result(fixed, spec, stdout="3\n", passing=True))
        view = self.resume(run)
        self.assertEqual((view["phase"], view["final_commit"]), ("completed", fixed))
        for role in ("coder", "cleaner", "architect", "hardener"):
            self.assertEqual(view["assignments"][f"{role}:3"]["receipt"]["result"]["commit"], fixed)

    def test_ordinary_qa_block_with_empty_commit_restarts_checks(self):
        run, spec, bad, _ = self.diagnosed_qa_failure()
        self.git("commit", "--quiet", "--allow-empty", "-m", "New commit before QA blocked")
        blocked = dict(input_commit=bad, commit=self.head(), issues="", checks=[],
                       specification=self.specification_id(spec), failure=dict(reason="Need more investigation."))
        self.report(run, "qa", 1, "blocked", blocked)
        self.cli(run, "rework")
        view = self.active(run, "coder", 2, self.head())
        self.assertNotIn("qa:2", view["assignments"])

    def test_changing_only_the_qa_step_requires_new_approval(self):
        run, old = self.waiting()
        self.approve(run, old)
        version_commit = self.write_spec(dict(QA_STEP, stdout="4\n"))
        self.cli(run, "recover")
        self.active(run, "coder", 1, old["commit"])
        yielded = dict(input_commit=old["commit"], commit=version_commit, issues="", checks=[],
                       specification=self.specification_id(old),
                       failure=dict(reason="The approved QA expectation changed."))
        self.report(run, "coder", 1, "superseded", yielded)
        self.active(run, "specifier", 2, version_commit)
        self.submit_spec(run, 2, version_commit)
        view = self.resume(run)
        new = view["specification"]
        self.assertEqual(view["phase"], "waiting-for-human")
        self.assertEqual(new["version"], old["version"] + 1)
        self.assertNotEqual(new["sha256"], old["sha256"])
        self.assertEqual(json.loads(old["body"])["examples"], json.loads(new["body"])["examples"])
        self.assertEqual(json.loads(new["body"])["qa_steps"], [dict(QA_STEP, stdout="4\n")])
        self.assertIsNone(new["approval"])
        self.assertIsNotNone(view["specification_versions"][0]["approval"])
        self.approve(run, old, code=1)
        self.approve(run, dict(new, sha256=old["sha256"]), code=1)
        self.assertNotIn("coder:2", self.resume(run)["assignments"])

        self.approve(run, new)
        self.active(run, "coder", 2, version_commit)
        self.write_target("number + 1", print_result="increment(number)")
        self.commit("Implement increment after reapproval")
        rejected = self.report(run, "coder", 2, "done", self.checks_for("coder", version_commit, old), code=1)
        self.assertIn("approved specification", rejected["rejection"])
        self.active(run, "coder", 2, version_commit)

    def test_repair_finding_must_use_the_assigned_qa_candidate(self):
        run, spec, bad, _ = self.diagnosed_qa_failure()
        self.write(self.project / "late-change.txt", "Unreviewed change after the QA assignment.\n")
        later = self.commit("Make a later, still broken candidate")
        result = self.qa_result(later, spec, stdout="2\n", passing=False)
        result["input_commit"] = bad
        result["issues"] = "The CLI prints its input instead of the computed value."
        rejected = self.report(run, "qa", 1, "repair", result, code=1)
        self.assertIn("assigned input commit", rejected["rejection"])
        self.active(run, "qa", 1, bad)

    def test_failed_local_repair_escalates_the_attempted_commit_and_finding(self):
        run, spec, bad = self.start_local_qa_repair()
        self.active(run, "qa", 2, bad)

        self.write(self.project / "attempt.txt", "An attempted repair that left the entry point broken.\n")
        attempted = self.commit("Attempt QA repair")
        still_failing = self.qa_result(attempted, spec, stdout="2\n", passing=False)
        still_failing["input_commit"] = bad
        self.report(run, "qa", 2, "fail", still_failing)
        view = self.active(run, "coder", 3, attempted)
        self.assertEqual(view["assignments"]["coder:3"]["source_receipt"],
                         view["assignments"]["qa:2"]["receipt"]["id"])
        self.assertEqual(view["assignments"]["qa:2"]["receipt"]["result"]["failure"], still_failing["failure"])
        message = self.worker_sends()[-1][1]
        self.assertIn("Observed QA mismatch", message)
        self.assertIn(USER_COMMAND, message.split("Observed QA mismatch", 1)[1])
        self.assertNotIn("coder:2", view["assignments"])

    def test_failed_repair_check_escalates_even_when_user_entry_now_passes(self):
        run, spec, bad = self.start_local_qa_repair()
        self.write_target("number + 1 if number == 2 else 0", print_result="increment(number)")
        attempted = self.commit("Make the example pass but break the property")
        result = self.qa_result(attempted, spec, stdout="3\n", passing=True)
        result["input_commit"] = bad
        result["checks"].append(self.check_result("qa", "properties", PROPERTIES, attempted, 1))
        result["failure"] = dict(check="properties", input="consecutive integers", expected="1", actual="0")
        self.report(run, "qa", 2, "fail", result)
        view = self.active(run, "coder", 3, attempted)
        receipt = view["assignments"]["qa:2"]["receipt"]["result"]
        self.assertEqual(receipt["failure"], result["failure"])
        self.assertEqual(receipt["qa"]["observations"], [QA_STEP])
        self.assertEqual(view["assignments"]["coder:3"]["source_receipt"],
                         view["assignments"]["qa:2"]["receipt"]["id"])

    def test_qa_specification_conflict_pauses_for_specifier_and_new_approval(self):
        run = self.init()["id"]
        self.cli(run, "start")
        self.write_spec(dict(QA_STEP, stdout="4\n"))
        self.submit_spec(run, 1, self.baseline)
        old = self.resume(run)["specification"]
        self.approve(run, old)
        self.write_target("number + 1", print_result="number")
        bad = self.commit("Implement before noticing inconsistent expectations")
        self.advance_to_qa(run, old, 1, old["commit"], bad)
        pause = dict(input_commit=bad, commit=bad, issues="", checks=[],
                     specification=self.specification_id(old),
                     failure=dict(reason="The example expects 3 but the approved user step expects 4."))
        self.report(run, "qa", 1, "paused", pause)
        self.assertEqual(self.resume(run)["phase"], "paused")
        self.cli(run, "recover")
        self.assertNotIn("coder:2", self.resume(run)["assignments"])
        self.cli(run, "rework", "--specification")
        self.active(run, "specifier", 2, bad)
        corrected = self.write_spec()
        self.submit_spec(run, 2, bad)
        view = self.resume(run)
        new = view["specification"]
        self.assertEqual(view["phase"], "waiting-for-human")
        self.assertEqual(new["commit"], corrected)
        self.assertEqual(new["version"], old["version"] + 1)
        self.assertNotEqual(new["sha256"], old["sha256"])
        self.assertIsNone(new["approval"])
        self.assertIsNotNone(view["specification_versions"][0]["approval"])
        self.approve(run, old, code=1)
        self.assertNotIn("coder:2", self.resume(run)["assignments"])
        self.approve(run, new)
        self.active(run, "coder", 2, corrected)

    def test_rejects_missing_role_requirements_and_early_completion(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        self.write_target("number + 1", print_result="increment(number)")
        commit = self.commit("Implement the increment command")
        coder = self.checks_for("coder", spec["commit"], spec)
        rejected = self.report(run, "coder", 1, "done", dict(coder, checks=coder["checks"][:1]), code=1)
        self.assertIn("missing required check acceptance", rejected["rejection"])
        self.active(run, "coder", 1, spec["commit"])
        self.report(run, "coder", 1, "done", coder)
        cleaner = self.checks_for("cleaner", commit, spec)
        rejected = self.report(run, "cleaner", 1, "done", dict(cleaner, checks=cleaner["checks"][:1]), code=1)
        self.assertIn("missing required check acceptance", rejected["rejection"])
        self.active(run, "cleaner", 1, commit)
        self.report(run, "cleaner", 1, "done", cleaner)
        architect = self.checks_for("architect", commit, spec)
        self.report(run, "architect", 1, "done", architect, code=1)
        architect["architecture"] = "The CLI delegates to the standalone increment function."
        self.report(run, "architect", 1, "done", dict(architect, checks=architect["checks"][:2]), code=1)
        self.report(run, "architect", 1, "pass", architect, code=1)
        self.active(run, "architect", 1, commit)
        self.report(run, "architect", 1, "done", architect)
        hardener = self.checks_for("hardener", commit, spec)
        self.report(run, "hardener", 1, "done", hardener, code=1)
        mutation = self.mutation_result(commit)
        incorrect_mutation = dict(mutation, assertion="unrelated failure")
        rejected = self.report(run, "hardener", 1, "done",
                               dict(hardener, checks=[*hardener["checks"], incorrect_mutation]), code=1)
        self.assertIn("declared assertion", rejected["rejection"])
        hardener["checks"].append(mutation)
        self.report(run, "hardener", 1, "pass", hardener, code=1)
        self.active(run, "hardener", 1, commit)
        self.report(run, "hardener", 1, "done", hardener)
        self.active(run, "qa", 1, commit)

    def test_rejects_missing_or_mismatched_qa_evidence(self):
        run, spec = self.waiting()
        self.approve(run, spec)
        self.write_target("number + 1", print_result="increment(number)")
        commit = self.commit("Implement the increment command")
        self.advance_to_qa(run, spec, 1, spec["commit"], commit)
        valid = self.qa_result(commit, spec, stdout="3\n", passing=True)
        alternatives = {
            "missing QA script and observations": {key: value for key, value in valid.items() if key != "qa"},
            "missing user entry check": dict(valid, checks=[]),
            "missing script": dict(valid, qa=dict(valid["qa"], script="missing.py")),
            "wrong script": dict(valid, qa=dict(valid["qa"], script=self.write(
                self.store / "other.py", "raise SystemExit(0)\n").name)),
            "wrong script output": dict(valid, qa=dict(valid["qa"], observations=[dict(QA_STEP, stdout="2\n")])),
            "wrong check commit": dict(valid, checks=[dict(valid["checks"][0], commit=spec["commit"])]),
        }
        original = json.loads((self.store / valid["checks"][0]["log"]).read_text(encoding="utf-8"))
        for field, override in (
            ("commit", spec["commit"]),
            ("script_sha256", "0" * 64),
            ("exit_code", 1),
            ("observations", [dict(QA_STEP, stderr="unexpected warning\n")]),
        ):
            name = self.json_file(self.store / f"bad-{field}.json", dict(original, **{field: override})).name
            alternatives[f"log {field}"] = dict(valid, checks=[dict(valid["checks"][0], log=name)])
        mismatch = deepcopy(valid)
        mismatch["qa"]["observations"][0]["stdout"] = "2\n"
        bad_log = self.json_file(self.store / "bad-observation.json",
                                 dict(original, observations=mismatch["qa"]["observations"]))
        mismatch["checks"][0]["log"] = bad_log.name
        alternatives["script and receipt agree on unapproved output"] = mismatch
        for name, payload in alternatives.items():
            with self.subTest(evidence=name):
                rejection = self.report(run, "qa", 1, "pass", payload, code=1)
                self.assertIn("rejection", rejection)
                self.active(run, "qa", 1, commit)
        contradictory = deepcopy(valid)
        contradictory["failure"] = dict(check="user-entry", input=USER_COMMAND, expected="3", actual="2")
        contradictory["checks"][0]["exit_code"] = 1
        contradictory_log = self.json_file(self.store / "contradictory-fail.json", dict(original, exit_code=1))
        contradictory["checks"][0]["log"] = contradictory_log.name
        rejected = self.report(run, "qa", 1, "fail", contradictory, code=1)
        self.assertIn("mismatch", rejected["rejection"])
        self.active(run, "qa", 1, commit)
        self.report(run, "qa", 1, "done", valid, code=1)
        self.active(run, "qa", 1, commit)

        self.write(self.project / "late-change.txt", "Change after Hardener's verification.\n")
        later = self.commit("Make an unreviewed change before the QA receipt")
        wrong_commit = self.qa_result(later, spec, stdout="3\n", passing=True)
        wrong_commit["input_commit"] = commit
        rejected = self.report(run, "qa", 1, "pass", wrong_commit, code=1)
        self.assertIn("assigned input commit", rejected["rejection"])
        self.active(run, "qa", 1, commit)
