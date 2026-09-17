from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from context_slice import __version__
from context_slice.onboarding import OnboardError, atomic_write, desired_release, onboard
from context_slice.runtime import RuntimeIntegrityError


SOURCE = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-slice-onboard-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home with spaces"
        self.home.mkdir()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(self.home / ".context-slice" / "run.py"), *args],
            capture_output=True, timeout=20,
        )

    def test_first_install_and_repeat_leave_existing_user_configuration(self):
        original = self.home / ".copilot" / "copilot-instructions.md"
        original.parent.mkdir()
        original.write_text("Existing user policy.\n", encoding="utf-8")
        first = onboard(SOURCE, self.home)
        self.assertTrue(first["changed"])
        self.assertTrue(first["up_to_date"])
        self.assertEqual(original.read_text(), "Existing user policy.\n")
        tracked = [Path(first["launcher"]), Path(first["instruction"]),
                   self.home / ".context-slice" / "current.json"]
        before = [p.stat().st_mtime_ns for p in tracked]
        second = onboard(SOURCE, self.home)
        self.assertFalse(second["changed"])
        self.assertEqual(before, [p.stat().st_mtime_ns for p in tracked])
        self.assertEqual(first["release"], second["release"])

    def test_check_is_read_only_and_reports_missing_installation(self):
        before = set(self.home.rglob("*"))
        report = onboard(SOURCE, self.home, check=True)
        self.assertFalse(report["up_to_date"])
        self.assertEqual(before, set(self.home.rglob("*")))
        onboard(SOURCE, self.home)
        self.assertTrue(onboard(SOURCE, self.home, check=True)["up_to_date"])

    def test_runtime_needs_neither_pip_nor_source_checkout(self):
        onboard(SOURCE, self.home)
        result = self.run_cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["fts5"])
        corpus = self.base / "notes"
        corpus.mkdir()
        (corpus / "sample.md").write_text("# Synthetic\nquartz answer\n", encoding="utf-8")
        output = self.run_cli("brief", "quartz", "--root", str(corpus), "--state-dir", str(self.base / "cache"))
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn("quartz answer", json.loads(output.stdout)["results"][0]["text"])

    def test_unowned_or_edited_instructions_are_preserved(self):
        target = self.home / ".copilot" / "instructions" / "context-slice.instructions.md"
        target.parent.mkdir(parents=True)
        target.write_text("Owner content\n")
        with self.assertRaises(OnboardError):
            onboard(SOURCE, self.home)
        self.assertEqual(target.read_text(), "Owner content\n")
        self.assertFalse((self.home / ".context-slice" / "current.json").exists())
        target.unlink()
        onboard(SOURCE, self.home)
        target.write_text("Owner edited managed content\n")
        with self.assertRaises(OnboardError):
            onboard(SOURCE, self.home)
        self.assertEqual(target.read_text(), "Owner edited managed content\n")

    def test_missing_owned_instruction_can_be_repaired(self):
        first = onboard(SOURCE, self.home)
        Path(first["instruction"]).unlink()
        self.assertTrue(onboard(SOURCE, self.home)["changed"])
        self.assertTrue(Path(first["instruction"]).exists())

    def test_corrupt_receipt_is_not_reset(self):
        onboard(SOURCE, self.home)
        target = self.home / ".context-slice" / "current.json"
        target.write_text('{"schema":999}')
        with self.assertRaises(OnboardError):
            onboard(SOURCE, self.home)
        self.assertEqual(target.read_text(), '{"schema":999}')

    def test_runtime_tampering_fails_before_execution(self):
        report = onboard(SOURCE, self.home)
        release = self.home / ".context-slice" / "releases" / report["release"]
        (release / "context_slice" / "engine.py").write_text("raise AssertionError('must never execute')")
        result = self.run_cli("doctor")
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed", json.loads(result.stderr)["message"])
        with self.assertRaises(RuntimeIntegrityError):
            onboard(SOURCE, self.home)

    def test_pointer_traversal_is_rejected(self):
        onboard(SOURCE, self.home)
        target = self.home / ".context-slice" / "current.json"
        pointer = json.loads(target.read_bytes())
        pointer["release"] = "../other"
        target.write_text(json.dumps(pointer))
        self.assertEqual(self.run_cli("doctor").returncode, 2)

    def test_upgrade_preserves_old_runtime_and_receipts(self):
        first = onboard(SOURCE, self.home)
        local_receipt = self.home / ".context-slice" / "unrelated-local-receipt.json"
        local_receipt.write_text('{"keep":true}')
        updated = self.base / "updated-source"
        shutil.copytree(SOURCE / "context_slice", updated / "context_slice", ignore=shutil.ignore_patterns("__pycache__"))
        version = updated / "context_slice" / "__init__.py"
        version.write_text(version.read_text().replace(__version__, '99.0.0'))
        second = onboard(updated, self.home)
        self.assertNotEqual(first["release"], second["release"])
        self.assertTrue((self.home / ".context-slice" / "releases" / first["release"]).exists())
        self.assertEqual(local_receipt.read_text(), '{"keep":true}')
        self.assertEqual(json.loads(self.run_cli("doctor").stdout)["version"], "99.0.0")

    def test_source_newlines_do_not_change_release_identity(self):
        lf_source = self.base / "lf-source"
        crlf_source = self.base / "crlf-source"
        for source in (lf_source, crlf_source):
            (source / "context_slice").mkdir(parents=True)
        for source_file in (SOURCE / "context_slice").glob("*.py"):
            data = source_file.read_bytes().replace(b"\r\n", b"\n")
            (lf_source / "context_slice" / source_file.name).write_bytes(data)
            (crlf_source / "context_slice" / source_file.name).write_bytes(data.replace(b"\n", b"\r\n"))
        self.assertEqual(desired_release(lf_source), desired_release(crlf_source))
        first = onboard(crlf_source, self.home)
        second = onboard(lf_source, self.home)
        self.assertEqual(first["release"], second["release"])
        self.assertFalse(second["changed"])
        self.assertNotIn(b"\r\n", Path(first["launcher"]).read_bytes())

    def test_activation_failure_rolls_back_only_owned_writes(self):
        original_write = atomic_write

        def fail_current(path, content, expected):
            if path.name == "current.json":
                raise OSError("injected atomic activation failure")
            return original_write(path, content, expected)

        with patch("context_slice.onboarding.atomic_write", side_effect=fail_current):
            with self.assertRaises(OSError):
                onboard(SOURCE, self.home)
        self.assertFalse((self.home / ".context-slice" / "run.py").exists())
        self.assertFalse((self.home / ".copilot" / "instructions" / "context-slice.instructions.md").exists())
        self.assertTrue(onboard(SOURCE, self.home)["up_to_date"])

    def test_parallel_onboarding_is_idempotent(self):
        command = [sys.executable, str(SOURCE / "onboard.py"), "--home", str(self.home)]
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        outputs = []
        for process in (first, second):
            output, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error)
            outputs.append(json.loads(output))
        self.assertEqual(sum(item["changed"] for item in outputs), 1)
        self.assertEqual(outputs[0]["release"], outputs[1]["release"])

    def test_linked_instruction_parent_is_refused(self):
        outside = self.base / "other-policy"
        outside.mkdir()
        (self.home / ".copilot").mkdir()
        try:
            (self.home / ".copilot" / "instructions").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is unavailable for this account.")
        with self.assertRaises(RuntimeIntegrityError):
            onboard(SOURCE, self.home)
        self.assertFalse((outside / "context-slice.instructions.md").exists())


if __name__ == "__main__":
    unittest.main()
