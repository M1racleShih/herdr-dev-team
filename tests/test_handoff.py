"""Check the installed entry point and pack CLI namespaces."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_two_pack import SCRIPT


class HandoffCliTest(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def test_help_lists_only_packs_and_delegates_pack_help(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "store"
            help_result = self.cli("--help")
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("STORE {two,four,six}", help_result.stdout)
            for pack in ("two", "four", "six"):
                with self.subTest(pack=pack):
                    run_help = self.cli(store, pack, "--help")
                    self.assertEqual(run_help.returncode, 0, run_help.stderr)
                    self.assertIn("resume", run_help.stdout)
                    init_help = self.cli(store, pack, "init", "--help")
                    self.assertEqual(init_help.returncode, 0, init_help.stderr)
                    self.assertIn("--checks", init_help.stdout)
                    if pack == "six":
                        self.assertIn("--plan", init_help.stdout)
            self.assertFalse(store.exists())

    def test_old_top_level_commands_leave_store_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "store"
            for command in ("init", "resume", "start", "report", "advance", "reconcile", "takeover"):
                with self.subTest(command=command):
                    result = self.cli(store, command)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("invalid choice", result.stderr)
                    self.assertFalse(store.exists())
