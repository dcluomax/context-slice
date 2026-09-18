from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from context_slice.engine import ContextError
from context_slice.onboarding import onboard
from context_slice.session import record_use, session_state


SOURCE = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-slice-session-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir()
        onboard(SOURCE, self.home)

    def test_receipt_does_not_infer_instruction_acknowledgement(self):
        before, path, _ = session_state(self.home, "task-1")
        self.assertFalse(before["recorded"])
        self.assertFalse(path.exists())
        first = record_use(self.home, "task-1")
        self.assertEqual(first["uses"], 1)
        self.assertFalse(first["instructions_acknowledged"])
        second = record_use(self.home, "task-1", first["instructions"])
        self.assertTrue(second["instructions_acknowledged"])
        self.assertEqual(second["uses"], 2)
        self.assertFalse(session_state(self.home, "other-task")[0]["recorded"])

    def test_wrong_instruction_hash_and_modified_receipts_are_errors(self):
        with self.assertRaises(ContextError):
            record_use(self.home, "task-1", "0" * 64)
        _, path, _ = session_state(self.home, "task-1")
        self.assertFalse(path.exists())
        record_use(self.home, "task-1")
        path.write_text('{"schema":999}')
        with self.assertRaises(ContextError):
            session_state(self.home, "task-1")

    def test_prepare_is_byte_bounded_and_does_not_store_query_or_corpus(self):
        corpus = self.home.parent / "notes"
        corpus.mkdir()
        (corpus / "note.md").write_text("# Sample\nquartz example\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(self.home / ".context-slice" / "run.py"),
             "prepare", "quartz", "--root", str(corpus), "--session", "task-1",
             "--home", str(self.home), "--state-dir", str(self.home.parent / "cache"),
             "--max-bytes", "2048"],
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 2048)
        value = json.loads(result.stdout)
        self.assertEqual(value["budget"]["returned_bytes"], len(result.stdout))
        self.assertTrue(value["activation"]["recorded"])
        self.assertTrue(value["results"])
        receipt = session_state(self.home, "task-1")[1].read_text()
        self.assertNotIn("quartz", receipt)
        self.assertNotIn(str(corpus), receipt)

    def test_session_identity_cannot_escape_receipt_directory(self):
        with self.assertRaises(ContextError):
            record_use(self.home, "../other")
