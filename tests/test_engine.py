from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from context_slice.engine import ContextError, Engine, MAX_FILE_BYTES, enable_wal, terms, wire


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-slice-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "notes"
        self.root.mkdir()
        self.state = self.base / "state"
        self.engine = Engine(self.root, self.state)
        self.addCleanup(self.engine.db.close)

    def note(self, path: str, text: str) -> Path:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
        return destination

    def test_incremental_refresh_and_exact_source_lines(self):
        self.note("guide.md", "# Guide\n\n## Rotation\nquartz rotation uses two slots.\n")
        first = self.engine.brief("quartz rotation")
        item = first["results"][0]
        self.assertEqual(item["path"], "guide.md")
        self.assertEqual(item["start_line"], 3)
        self.assertEqual(item["end_line"], 4)
        self.assertEqual(item["text"], "## Rotation\nquartz rotation uses two slots.\n")
        self.assertEqual(first["refresh"]["read_files"], 1)
        second = self.engine.brief("quartz rotation")
        self.assertEqual(second["refresh"]["read_files"], 0)
        self.assertEqual(second["budget"]["returned_bytes"], len(wire(second)))

    def test_edits_and_deletions_update_index(self):
        path = self.note("guide.md", "# Old\nquartz old fact\n")
        self.engine.brief("quartz")
        path.write_text("# New\nquartz new fact\n", encoding="utf-8")
        self.assertIn("new fact", self.engine.brief("quartz")["results"][0]["text"])
        path.unlink()
        self.assertEqual(self.engine.brief("quartz")["results"], [])

    def test_crlf_is_preserved(self):
        self.note("windows.md", "").write_bytes(b"# Guide\r\nquartz answer\r\n")
        item = self.engine.brief("quartz")["results"][0]
        self.assertEqual(item["text"], "# Guide\r\nquartz answer\r\n")
        self.assertEqual(item["start_line"], 1)
        self.assertEqual(item["end_line"], 2)

    def test_scope_is_a_path_boundary(self):
        self.note("202601/1/a.md", "# Guide\nquartz intended\n")
        self.note("202601/10/a.md", "# Guide\nquartz not selected\n")
        result = self.engine.brief("quartz", scope="202601\\1")
        self.assertEqual(result["refresh"]["visited_files"], 1)
        self.assertEqual(result["results"][0]["path"], "202601/1/a.md")
        self.engine.refresh()
        self.assertEqual(len(self.engine.brief("quartz", scope="202601/1")["results"]), 1)

    def test_stopwords_and_cjk(self):
        self.note("english.md", "# Rotation\nquartz rotation guard\n")
        self.note("chinese.md", "# \u68c0\u7d22\n\u51cf\u5c11\u4e0a\u4e0b\u6587\u4ee3\u5e01\u6d88\u8017\n")
        self.assertEqual(terms("the and is of"), [])
        self.assertEqual(self.engine.brief("how does the quartz rotation work")["results"][0]["path"], "english.md")
        self.assertEqual(self.engine.brief("\u4e0a\u4e0b\u6587")["results"][0]["path"], "chinese.md")
        with self.assertRaises(ContextError):
            self.engine.brief("the and")

    def test_queries_are_data_not_fts_syntax(self):
        self.note("guide.md", "# Guide\nquartz answer\n")
        result = self.engine.brief('quartz" OR * NOT (bad)')
        self.assertTrue(result["results"])
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM documents").fetchone()[0], 1)

    def test_explicit_acknowledgement_and_context_reset(self):
        self.note("guide.md", "# Guide\nquartz answer\n")
        first = self.engine.brief("quartz", session="session-one")
        second = self.engine.brief("quartz", session="session-one")
        self.assertTrue(second["results"], "Merely delivering a packet cannot suppress its contents.")
        self.engine.acknowledge("session-one", first["delivery_id"])
        third = self.engine.brief("quartz", session="session-one")
        self.assertEqual(third["results"], [])
        self.assertEqual(len(third["already_read"]), 1)
        self.assertTrue(self.engine.brief("quartz", session="different-session")["results"])
        self.engine.forget("session-one")
        self.assertTrue(self.engine.brief("quartz", session="session-one")["results"])

    def test_stale_and_wrong_session_acknowledgements_fail(self):
        path = self.note("guide.md", "# Guide\nquartz old\n")
        packet = self.engine.brief("quartz", session="owner")
        with self.assertRaises(ContextError):
            self.engine.acknowledge("other", packet["delivery_id"])
        path.write_text("# Guide\nquartz new\n", encoding="utf-8")
        with self.assertRaisesRegex(ContextError, "Source changed"):
            self.engine.acknowledge("owner", packet["delivery_id"])
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)

    def test_edit_reemits_acknowledged_source(self):
        path = self.note("guide.md", "# Guide\nquartz old\n")
        packet = self.engine.brief("quartz", session="owner")
        self.engine.acknowledge("owner", packet["delivery_id"])
        path.write_text("# Guide\nquartz new\n", encoding="utf-8")
        self.assertIn("new", self.engine.brief("quartz", session="owner")["results"][0]["text"])

    def test_selected_sources_are_hashed_even_when_stat_unchanged(self):
        path = self.note("guide.md", "# Guide\nquartz old\n")
        self.engine.refresh()
        path.write_text("# Guide\nquartz new\n", encoding="utf-8")
        with patch.object(self.engine, "refresh", return_value={"read_files": 0}):
            with self.assertRaisesRegex(ContextError, "indexed source changed"):
                self.engine.brief("quartz")

    def test_explicit_verify_rehashes_unchanged_files(self):
        self.note("guide.md", "# Guide\nquartz old\n")
        self.engine.refresh()
        self.assertEqual(self.engine.refresh(verify=True)["read_files"], 1)

    def test_excerpt_centers_on_strongest_matching_line(self):
        self.note("guide.md", "# Rotation\n" + "unrelated line\n" * 20 + "quartz rotation exact answer\n")
        self.assertIn("exact answer", self.engine.brief("quartz rotation")["results"][0]["text"])

    def test_byte_budget_includes_unicode_json_envelope(self):
        self.note("guide.md", "# Guide\nquartz " + "\u4e2d" * 1200 + "\n")
        for budget in (1024, 2048, 4096, 8192):
            result = self.engine.brief("quartz", max_bytes=budget)
            self.assertLessEqual(len(wire(result)), budget)
            self.assertEqual(result["budget"]["returned_bytes"], len(wire(result)))
            self.assertTrue(result["results"] or result["omitted"]["budget"])

    def test_large_line_is_not_silently_cut(self):
        self.note("guide.md", "# Guide\nquartz " + "x" * 12000 + " essential ending\n")
        result = self.engine.brief("quartz", max_bytes=2048)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["omitted"]["budget"], 1)
        self.assertEqual(result["needs_read"][0]["path"], "guide.md")
        self.assertIsNone(result["delivery_id"])
        with self.assertRaises(ContextError):
            self.engine.read("guide.md", 1, 2, 2048)
        self.assertIn("essential ending", self.engine.read("guide.md", 2, 2, 16000)["text"])

    def test_default_exclusions_and_coverage(self):
        self.note("node_modules/dependency.md", "quartz exclude")
        self.note(".private/secret.md", "quartz exclude")
        self.note("secrets.md", "quartz exclude")
        self.note("actual.md", "quartz include")
        self.note("binary.md", "quartz\0binary")
        self.note("oversized.md", "x" * (MAX_FILE_BYTES + 1))
        self.note("not-utf8.md", "").write_bytes(b"\xffquartz")
        result = self.engine.brief("quartz")
        self.assertEqual([item["path"] for item in result["results"]], ["actual.md"])
        self.assertEqual(result["refresh"]["skipped"], {"binary": 1, "oversized": 1, "non_utf8": 1})
        with self.assertRaises(ContextError):
            self.engine.read("secrets.md", 1, 1, 2048)

    def test_traversal_absolute_paths_and_hidden_reads_rejected(self):
        for scope in ("../notes", "/tmp", "C:\\outside", "a/../b", ".private", "a//b"):
            with self.subTest(scope=scope), self.assertRaises((ContextError, FileNotFoundError)):
                self.engine.brief("quartz", scope=scope)

    def test_links_are_excluded(self):
        outside = self.base / "outside.md"
        outside.write_text("quartz private\n", encoding="utf-8")
        link = self.root / "linked.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symbolic links is unavailable for this account.")
        self.assertEqual(self.engine.brief("quartz")["results"], [])
        with self.assertRaises(ContextError):
            self.engine.read("linked.md", 1, 1, 2048)

    def test_state_outside_corpus_and_git(self):
        with self.assertRaises(ContextError):
            Engine(self.root, self.root / "cache")
        other = self.base / "git-project"
        (other / ".git").mkdir(parents=True)
        with self.assertRaises(ContextError):
            Engine(self.root, other / "cache")

    def test_root_binding_and_unknown_schema(self):
        other = self.base / "other"
        other.mkdir()
        with self.assertRaises(ContextError):
            Engine(other, self.state)
        self.engine.db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ContextError, "Unsupported"):
            Engine(self.root, self.state)
        self.assertEqual(self.engine.db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_outline_and_range_hash_guard(self):
        self.note("guide.md", "# Guide\nquartz\n## Second\nanswer\n")
        outline = self.engine.outline("guide.md", 2048)
        self.assertEqual(outline["sections"], [{"line": 1, "heading": "Guide"}, {"line": 3, "heading": "Second"}])
        content = self.engine.read("guide.md", 3, 4, 2048, outline["sha256"])
        self.assertEqual(content["text"], "## Second\nanswer\n")
        with self.assertRaises(ContextError):
            self.engine.read("guide.md", 1, 4, 2048, "stale")

    def test_outline_preserves_duplicate_headings_and_ignores_fenced_code(self):
        self.note("guide.md", "# Repeated\n````python\n# Not a heading\n```\n# Still code\n````\n# Repeated\n")
        result = self.engine.outline("guide.md", 2048)
        self.assertEqual(result["sections"], [{"line": 1, "heading": "Repeated"}, {"line": 7, "heading": "Repeated"}])

    def test_source_status_and_raw_evidence_are_not_promoted(self):
        self.note("evidence/raw.md", "Status: superseded\n# Report\nquartz obsolete finding\n")
        item = self.engine.brief("quartz")["results"][0]
        self.assertEqual(item["source_declared_status"], "superseded")
        self.assertEqual(item["source_kind"], "raw_evidence")

    def test_raw_marker_carries_to_later_excerpts(self):
        self.note("source.md", "<!-- pkc-evidence:raw-transcript -->\n" +
                  "background\n" * 50 + "## Finding\nquartz reported claim\n")
        self.assertEqual(self.engine.brief("quartz")["results"][0]["source_kind"], "raw_evidence")

    def test_concurrent_first_use_serializes_initialization(self):
        fresh = self.base / "concurrent-state"
        self.note("guide.md", "# Guide\nquartz answer\n")
        command = [sys.executable, "-m", "context_slice", "brief", "quartz",
                   "--root", str(self.root), "--state-dir", str(fresh)]
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for process in (first, second):
            output, error = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, error)
            self.assertTrue(json.loads(output)["results"])

    def test_journal_busy_is_retried_before_schema_initialization(self):
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        connection = MagicMock()
        row = MagicMock()
        row.fetchone.return_value = ("wal",)
        connection.execute.side_effect = [busy, row]
        with patch("context_slice.engine.time.sleep") as sleep:
            enable_wal(connection)
        self.assertEqual(connection.execute.call_count, 2)
        sleep.assert_called_once()

    def test_journal_busy_wait_is_bounded(self):
        busy = sqlite3.OperationalError("database is locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        connection = MagicMock()
        connection.execute.side_effect = busy
        with self.assertRaisesRegex(ContextError, "remained busy"):
            enable_wal(connection, timeout_seconds=0)
        self.assertEqual(connection.execute.call_count, 1)

    def test_journal_io_errors_are_not_retried_or_hidden(self):
        failure = sqlite3.OperationalError("disk I/O error")
        failure.sqlite_errorcode = sqlite3.SQLITE_IOERR
        connection = MagicMock()
        connection.execute.side_effect = failure
        with self.assertRaises(sqlite3.OperationalError):
            enable_wal(connection)
        self.assertEqual(connection.execute.call_count, 1)

    def test_cli_output_is_json_and_errors_are_not_success(self):
        self.note("guide.md", "# Guide\nquartz answer\n")
        base = [sys.executable, "-m", "context_slice", "brief",
                "--root", str(self.root), "--state-dir", str(self.state)]
        success = subprocess.run(base + ["quartz"], capture_output=True, timeout=15)
        self.assertEqual(success.returncode, 0, success.stderr)
        payload = json.loads(success.stdout)
        self.assertEqual(payload["budget"]["returned_bytes"], len(success.stdout))
        failure = subprocess.run(base + ["and the"], capture_output=True, timeout=15)
        self.assertEqual(failure.returncode, 2)
        self.assertEqual(failure.stdout, b"")
        self.assertIn("error", json.loads(failure.stderr))


if __name__ == "__main__":
    unittest.main()
