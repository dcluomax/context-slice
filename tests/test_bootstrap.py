from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import bootstrap
from context_slice.onboarding import desired_release, onboard
from context_slice.runtime import canonical, sha256


SOURCE = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-slice-bootstrap-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        manifest, _, _ = desired_release(SOURCE)
        self.pin = {
            "schema": 1, "repository": bootstrap.REPOSITORY,
            "revision": "a" * 40, "version": manifest["version"],
            "fingerprint": sha256(canonical(manifest)),
        }

    def fake_fetch(self, destination, pin):
        shutil.copytree(SOURCE / "context_slice", destination / "context_slice",
                        ignore=shutil.ignore_patterns("__pycache__"))

    def test_steady_state_has_no_network_or_writes(self):
        onboard(SOURCE, self.home)
        before = (self.home / ".context-slice" / "current.json").stat().st_mtime_ns
        with patch("bootstrap.fetch_source") as fetch:
            result = bootstrap.ensure(self.home, self.pin)
        fetch.assert_not_called()
        self.assertTrue(result["up_to_date"])
        self.assertFalse(result["changed"])
        self.assertEqual(before, (self.home / ".context-slice" / "current.json").stat().st_mtime_ns)

    def test_missing_check_does_not_install(self):
        with patch("bootstrap.fetch_source") as fetch:
            result = bootstrap.ensure(self.home, self.pin, check=True)
        self.assertFalse(result["up_to_date"])
        fetch.assert_not_called()
        self.assertFalse((self.home / ".context-slice").exists())

    def test_bootstrap_verifies_before_install_and_becomes_noop(self):
        with patch("bootstrap.fetch_source", side_effect=self.fake_fetch) as fetch:
            first = bootstrap.ensure(self.home, self.pin)
            second = bootstrap.ensure(self.home, self.pin)
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["network_fetches"], 1)
        self.assertEqual(second["network_fetches"], 0)

    def test_wrong_source_fingerprint_never_executes_download(self):
        self.pin["fingerprint"] = "0" * 64
        with patch("bootstrap.fetch_source", side_effect=self.fake_fetch):
            with patch("bootstrap.subprocess.run") as run:
                with self.assertRaisesRegex(bootstrap.BootstrapError, "no downloaded Python code"):
                    bootstrap.ensure(self.home, self.pin)
        run.assert_not_called()
        self.assertFalse((self.home / ".context-slice").exists())

    def test_arbitrary_repository_and_short_revision_are_rejected(self):
        path = self.base / "release.json"
        for field, value in (
            ("repository", "https://example.invalid/other.git"),
            ("revision", "main"), ("fingerprint", "../elsewhere"),
        ):
            candidate = dict(self.pin, **{field: value})
            path.write_text(json.dumps(candidate))
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.read_pin(path)

    def test_unowned_instruction_is_not_overwritten_by_bootstrap(self):
        target = self.home / ".copilot" / "instructions" / "context-slice.instructions.md"
        target.parent.mkdir(parents=True)
        target.write_text("local policy")
        with patch("bootstrap.fetch_source", side_effect=self.fake_fetch):
            with self.assertRaises(bootstrap.subprocess.CalledProcessError):
                bootstrap.ensure(self.home, self.pin)
        self.assertEqual(target.read_text(), "local policy")


if __name__ == "__main__":
    unittest.main()
