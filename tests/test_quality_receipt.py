"""Exercise latest-execution and origin rules for plan receipts."""

from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/herdr-dev-team/scripts"))
from pack_store import SCHEMA, encode
from quality_receipt import validate_quality
import quality_store


class QualityReceiptTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA + quality_store.SCHEMA)
        self.run = {"id": "run"}
        self.owner = {"id": "run/hardener/1", "role": "hardener"}
        self.qa = {"id": "run/qa/1", "role": "qa"}
        self.spec = {"version": 1, "sha256": "b" * 64}
        self.commit = "a" * 40
        self.check = {"id": "compat", "owner": "hardener", "use": "final", "argv": ["./check"]}
        self.plan = {"plan_version": 1, "plan": {"checks": [self.check]}}

    def execution(self, assignment, key, outcome):
        execution = quality_store.register_execution(
            self.db, self.run, assignment, self.plan, self.spec, self.check, self.commit,
            key, None, "run-dir", SimpleNamespace(st_dev=0, st_ino=0),
        )
        quality_store.finish_execution(
            self.db, execution, outcome=outcome, summary=outcome, return_code=0,
            tool="./check", configuration="fixture", evidence={},
        )
        return execution

    def payload(self, *executions, **extra):
        return {"commit": self.commit, "quality": {"plan_version": 1, "executions": list(executions)}, **extra}

    def accept(self, assignment, status, payload):
        value = encode(payload)
        self.db.execute(
            "INSERT INTO receipts (assignment_id, status, worker, received_at, submitted, payload, accepted) "
            "VALUES (?, ?, 'worker', '', ?, ?, 1)",
            (assignment["id"], status, value, value),
        )

    def validate(self, assignment, status, payload):
        return validate_quality(self.db, self.run, assignment, self.plan, self.spec, status, payload)

    def test_qa_final_must_cite_its_latest_execution(self):
        old = self.execution(self.owner, "old", "pass")
        self.accept(self.owner, "done", self.payload(old))
        self.assertEqual(self.validate(self.qa, "pass", self.payload()), (None, {"compat": old}))
        current = self.execution(self.qa, "current", "pass")
        with self.assertRaisesRegex(ValueError, "must reference the latest execution"):
            self.validate(self.qa, "pass", self.payload(old))
        with self.assertRaisesRegex(ValueError, "is superseded by"):
            self.validate(self.qa, "pass", self.payload(old, current))
        self.assertEqual(self.validate(self.qa, "pass", self.payload(current)), (None, {"compat": current}))

    def test_fail_cannot_fall_back_to_an_older_red_execution(self):
        old = self.execution(self.owner, "old", "fail")
        failure = self.payload(old, failure={"check": "compat"})
        self.assertEqual(self.validate(self.owner, "fail", failure), ("compat", None))
        for outcome in ("inconclusive", "pass"):
            with self.subTest(outcome=outcome):
                self.execution(self.owner, outcome, outcome)
                with self.assertRaisesRegex(ValueError, f"latest execution.* is {outcome}"):
                    self.validate(self.owner, "fail", failure)

    def test_only_an_accepted_owner_can_supply_an_inherited_final(self):
        old_qa = {"id": "run/qa/0", "role": "qa"}
        execution = self.execution(old_qa, "cross-role", "pass")
        self.accept(old_qa, "paused", self.payload(execution))
        with self.assertRaisesRegex(ValueError, "accepted receipt from its owner"):
            self.validate(self.qa, "pass", self.payload())
        with self.assertRaisesRegex(ValueError, "wrong source assignment"):
            self.validate(self.qa, "pass", self.payload(execution))


if __name__ == "__main__":
    unittest.main()
