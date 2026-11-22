"""Exercise the approved examples against the two-pack resume CLI."""

import json
from pathlib import Path
import unittest

from test_two_pack import TwoPackFixture


EXAMPLES = Path(__file__).resolve().parent / "fixtures" / "resume-examples.json"


class ApprovedResumeTests(TwoPackFixture):
    @classmethod
    def setUpClass(cls):
        examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]
        cls.examples = {example["id"]: example for example in examples}

    def deliver_to_cleaner(self):
        run = self.start()
        coder_commit = self.commit("Coder delivery")
        report = self.report(run, "coder", 1, "done", self.result())
        self.assertTrue(report["receipt_saved"], report)
        return run, coder_commit

    def accept_cleaner_pass(self):
        run, coder_commit = self.deliver_to_cleaner()
        cleaner_commit = self.commit("Cleaner improvement")
        result = self.result("cleaner", "pass", input_commit=coder_commit)
        report = self.report(run, "cleaner", 1, "pass", result)
        self.assertTrue(report["receipt_saved"], report)
        return run, cleaner_commit

    def assert_example_summary(self, example_id, run, commit):
        expected = self.examples[example_id]["expected_summary"].format(short_commit=commit[:7])
        self.assertEqual(self.resume(run)["summary"], expected,
                         f"approved resume summary for {example_id}")

    def test_cleaner_delivered(self):
        self.assert_example_summary("cleaner_delivered", *self.deliver_to_cleaner())

    def test_cleaner_accepted(self):
        self.assert_example_summary("cleaner_accepted", *self.accept_cleaner_pass())


if __name__ == "__main__":
    unittest.main()
