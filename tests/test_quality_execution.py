"""Cover subprocess classification, archive confinement and execution liveness."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest

from test_two_pack import SCRIPT

sys.path.insert(0, str(SCRIPT.parent))
import quality_exec


class QualityExecutionTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="quality execution ")
        self.addCleanup(scratch.cleanup)
        self.store = Path(scratch.name)
        self.project = self.store / "project"
        self.project.mkdir()
        self.spec = {"path": "approved examples.json"}
        self.commit = "a" * 40

    def execute(self, definition):
        with ExitStack() as stack:
            directory, lock_fd = quality_exec.allocate_check(stack, self.store)
            code, error = quality_exec.execute(directory, self.project, self.spec, self.commit, definition, lock_fd)
            return quality_exec.conclude(self.store, directory, definition, code, error)

    def test_exit_protocol_captures_real_status_argv_and_output(self):
        code = """
import json
import os
import sys
print(json.dumps([os.getcwd(), os.environ["HERDR_CHECK_COMMIT"], os.environ["HERDR_APPROVED_SPEC"],
                  *sys.argv[2:]]))
print("diagnostic", file=sys.stderr)
sys.exit(int(sys.argv[1]))
"""
        for status, outcome in ((0, "pass"), (5, "fail"), (6, "inconclusive")):
            with self.subTest(status=status):
                result = self.execute({"protocol": "exit", "fail_codes": [5], "argv": [
                    sys.executable, "-c", code, str(status), "argument with spaces", "", "$literal",
                ]})
                self.assertEqual((result["outcome"], result["return_code"]), (outcome, status))
                evidence = result["evidence"]
                output = json.loads((self.store / evidence["stdout"]["path"]).read_text())
                self.assertEqual(output, [str(self.project), self.commit, str(self.project / self.spec["path"]),
                                          "argument with spaces", "", "$literal"])
                self.assertEqual((self.store / evidence["stderr"]["path"]).read_text(), "diagnostic\n")

    def test_start_failure_signal_and_timeout_are_inconclusive(self):
        cases = (
            ([str(self.project / "missing-program")], {}, None, "could not start check command"),
            ([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
             {}, -signal.SIGTERM, "signal"),
            ([sys.executable, "-c", "import time; time.sleep(60)"],
             {"timeout_seconds": 1}, -signal.SIGKILL, "timed out"),
        )
        for argv, settings, status, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                result = self.execute({"protocol": "exit", "fail_codes": [1], "argv": argv, **settings})
                self.assertEqual((result["outcome"], result["return_code"]), ("inconclusive", status))
                self.assertIn(diagnostic, result["summary"])

    def test_adapter_result_rejects_its_own_aliases_and_external_evidence(self):
        code = """
import json
import os
from pathlib import Path
import sys
result = Path(os.environ["HERDR_CHECK_RESULT"])
artifact = result.parent / "native.txt"
result.write_text(json.dumps(dict(outcome="pass", summary="all samples passed", tool="fixture",
                                  configuration="all samples", artifacts=[artifact.name])))
if sys.argv[1] == "hardlink":
    artifact.hardlink_to(result)
elif sys.argv[1] == "symlink":
    artifact.symlink_to(result)
elif sys.argv[1] == "outside":
    artifact.symlink_to(Path.cwd() / "unrelated.txt")
else:
    artifact.write_text("observed samples")
"""
        (self.project / "unrelated.txt").write_text("foreign evidence")
        for mode in ("hardlink", "symlink", "outside", "native"):
            with self.subTest(mode=mode):
                result = self.execute({"protocol": "result", "argv": [sys.executable, "-c", code, mode]})
                self.assertIn("result", result["evidence"])
                if mode == "native":
                    self.assertEqual(result["outcome"], "pass")
                    artifact = result["evidence"]["artifacts"][0]
                    self.assertEqual((self.store / artifact["path"]).read_text(), "observed samples")
                else:
                    self.assertEqual(result["outcome"], "inconclusive")
                    self.assertEqual(result["evidence"]["artifacts"], [])

    def test_missing_or_replaced_process_lock_leaves_liveness_unknown(self):
        with ExitStack() as stack:
            directory, fd = quality_exec.allocate_check(stack, self.store)
            info = os.fstat(fd)
            row = {"directory": str(directory.relative_to(self.store)),
                   "lock_device": info.st_dev, "lock_inode": info.st_ino}
            lock = directory / "process.lock"
            original = lock.with_suffix(".original")
            self.assertEqual(quality_exec.process_status(self.store, row), "running")
            lock.rename(original)
            self.assertEqual(quality_exec.process_status(self.store, row), "unknown")
            lock.touch()
            self.assertEqual(quality_exec.process_status(self.store, row), "unknown")
            original.replace(lock)
            self.assertEqual(quality_exec.process_status(self.store, row), "running")
        self.assertEqual(quality_exec.process_status(self.store, row), "interrupted")


if __name__ == "__main__":
    unittest.main()
