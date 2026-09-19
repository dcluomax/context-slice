from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from context_slice.controls import ControlError, ControlStore, WithdrawnError, ZERO
from context_slice.engine import ContextError, Engine, MAX_FILE_BYTES, digest, terms
from context_slice.onboarding import onboard


SOURCE = Path(__file__).resolve().parents[1]


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-slice-controls-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "corpus" / "notes"
        self.root.mkdir(parents=True)
        self.state = self.base / "cache"
        self.store = ControlStore(self.home)
        self.engine = Engine(self.root, self.state, home=self.home)
        self.addCleanup(self.engine.db.close)
        self.raw = b"# Quartz\nquartz retained source\n"
        self.path = self.note("team/a.md")
        self.sha = digest(self.raw)

    def note(self, relative, raw=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.raw if raw is None else raw)
        return path

    def enable(self):
        return self.store.enable()

    def withdraw(self, request="w1", scope="team", revision=None):
        return self.engine.withdraw(
            "team/a.md", self.sha, scope, request,
            revision or self.store.snapshot().revision,
        )

    def reinstate(self, target="w1", request="r1"):
        return self.store.change(
            "reinstate", request, self.store.snapshot().revision, target=target,
        )

    def test_disabled_controls_do_not_create_state_or_resolve_each_document(self):
        self.assertTrue(self.engine.brief("quartz")["results"])
        self.assertFalse(self.store.directory.exists())
        with patch("context_slice.controls.location", side_effect=AssertionError("unnecessary filesystem work")):
            self.assertFalse(self.store.snapshot().denies(self.path, self.sha))

    def test_enable_is_explicit_and_idempotent(self):
        first = self.enable()
        self.assertTrue(first["enabled"])
        self.assertEqual(first["revision"], ZERO)
        self.assertEqual(self.enable(), first)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_all_content_entry_points_and_acknowledgement_obey_withdrawals(self):
        self.enable()
        delivery = self.engine.brief("quartz", session="owner")
        self.withdraw()
        self.assertEqual(self.engine.candidates(terms("quartz"), "quartz", "")[0], [])
        for operation in (
            lambda: self.engine.source("team/a.md"),
            lambda: self.engine.read("team/a.md", 1, 10, 8192),
            lambda: self.engine.outline("team/a.md", 8192),
            lambda: self.engine.acknowledge("owner", delivery["delivery_id"]),
        ):
            with self.assertRaises(WithdrawnError):
                operation()
        packet = self.engine.brief("quartz", session="owner")
        self.assertEqual(packet["results"], [])
        self.assertEqual(packet["already_read"], [])
        self.assertEqual(packet["refresh"]["skipped"], {"withdrawn": 1})
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM fragments").fetchone()[0], 0)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_exact_copies_and_restored_sources_stay_blocked_with_a_new_cache(self):
        self.enable()
        self.withdraw()
        self.path.unlink()
        self.engine.refresh()
        self.path.write_bytes(self.raw)
        self.note("team/copied.md")
        with Engine(self.root, self.base / "new-cache", home=self.home) as fresh:
            packet = fresh.brief("quartz")
            self.assertEqual(packet["results"], [])
            self.assertEqual(packet["refresh"]["skipped"], {"withdrawn": 2})

    def test_scope_boundary_does_not_suppress_neighbors_or_other_roots(self):
        self.note("team2/a.md")
        self.enable()
        self.withdraw()
        self.assertEqual([x["path"] for x in self.engine.brief("quartz")["results"]], ["team2/a.md"])
        other = self.base / "other"
        other.mkdir()
        (other / "a.md").write_bytes(self.raw)
        with Engine(other, self.base / "other-cache", home=self.home) as engine:
            self.assertTrue(engine.brief("quartz")["results"])

    def test_parent_and_nested_lookup_roots_do_not_bypass_absolute_scope(self):
        self.enable()
        self.withdraw()
        for index, root in enumerate((self.root / "team", self.root.parent)):
            with Engine(root, self.base / f"cache-{index}", home=self.home) as engine:
                self.assertEqual(engine.brief("quartz")["results"], [])

    def test_case_aliases_follow_the_native_filesystem_not_a_posix_assumption(self):
        alternate = self.root.parent / self.root.name.upper()
        if not alternate.exists() or not alternate.samefile(self.root):
            self.skipTest("This volume is case-sensitive.")
        self.enable()
        self.withdraw()
        with Engine(alternate, self.base / "case-alias-cache", home=self.home) as engine:
            self.assertEqual(engine.brief("quartz")["results"], [])
            self.assertFalse(self.store.snapshot().denies(engine.root / "team2/a.md", self.sha))

    def test_changed_bytes_are_an_explicit_nonsemantic_boundary(self):
        self.enable()
        self.withdraw()
        self.path.write_bytes(self.raw.replace(b"\n", b"\r\n"))
        self.assertTrue(self.engine.brief("quartz")["results"])

    def test_reinstatement_clears_only_its_referenced_denial(self):
        self.enable()
        self.withdraw()
        self.withdraw("w2")
        self.reinstate()
        replay = self.store.change("reinstate", "r1", ZERO, target="w1")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["read_seq"], 3)
        self.assertEqual(self.engine.brief("quartz")["results"], [])
        self.reinstate("w2", "r2")
        self.assertTrue(self.engine.brief("quartz")["results"])
        with self.assertRaises(ControlError):
            self.reinstate("w1", "r3")

    def test_library_source_never_hashes_and_serves_an_oversized_prefix(self):
        self.enable()
        oversized = b"a" * (MAX_FILE_BYTES + 132)
        self.path.write_bytes(oversized)
        self.store.change("withdraw", "large", ZERO, scope=self.path, sha256=digest(oversized))
        with self.assertRaisesRegex(ContextError, "no prefix"):
            self.engine.source("team/a.md")

    @unittest.skipUnless(os.name == "nt", "Windows filesystem spelling")
    def test_windows_source_and_scope_spelling_are_canonicalized(self):
        self.enable()
        self.engine.withdraw("TEAM/A.MD", self.sha.upper(), "TEAM", "mixed-case", ZERO)
        self.assertEqual(self.engine.brief("quartz")["results"], [])

    def test_engine_construction_does_not_duplicate_per_operation_chain_validation(self):
        self.enable()
        with patch.object(ControlStore, "snapshot", wraps=self.store.snapshot) as snapshot:
            with Engine(self.root, self.base / "single-read-cache", home=self.home) as engine:
                self.assertEqual(snapshot.call_count, 0)
                engine.brief("quartz")
                self.assertEqual(snapshot.call_count, 1)

    def test_stale_source_and_scope_are_rejected_before_mutation(self):
        self.enable()
        with self.assertRaises(ContextError):
            self.engine.withdraw("team/a.md", ZERO, "team", "bad", ZERO)
        self.note("team2/b.md")
        with self.assertRaises(ContextError):
            self.withdraw(scope="team2")
        self.assertEqual(self.store.snapshot().sequence, 0)

    def test_commit_revision_is_checked_inside_sqlite_transaction(self):
        self.enable()
        self.withdraw()
        with self.assertRaisesRegex(ControlError, "changed before commit"):
            self.withdraw("stale-writer", revision=ZERO)
        self.assertEqual(self.store.snapshot().sequence, 1)

    def test_simultaneous_writers_with_the_same_expected_revision_have_one_winner(self):
        self.enable()
        barrier = threading.Barrier(2)

        def write(request):
            barrier.wait(timeout=10)
            try:
                return ControlStore(self.home).change(
                    "withdraw", request, ZERO, scope=self.root / "team", sha256=self.sha,
                )["read_seq"]
            except ControlError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ("parallel-1", "parallel-2")))
        self.assertCountEqual(results, [1, "rejected"])
        self.assertEqual(self.store.snapshot().sequence, 1)

    def test_process_exit_before_commit_rolls_back_and_lost_reply_is_idempotent(self):
        self.enable()
        program = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from context_slice.controls import ControlStore, ZERO
store = ControlStore(Path(sys.argv[2]))
original = ControlStore._load
calls = 0
def interrupted(connection):
    global calls
    calls += 1
    if calls == 2:
        os._exit(23)
    return original(connection)
if sys.argv[5] == "before":
    ControlStore._load = staticmethod(interrupted)
store.change("withdraw", "interrupted", ZERO, scope=Path(sys.argv[3]), sha256=sys.argv[4])
os._exit(24)
"""
        command = [
            sys.executable, "-I", "-B", "-c", program, str(SOURCE), str(self.home),
            str(self.root / "team"), self.sha,
        ]
        interrupted = subprocess.run(command + ["before"], capture_output=True, timeout=20)
        self.assertEqual(interrupted.returncode, 23, interrupted.stderr)
        self.assertEqual(self.store.snapshot().sequence, 0)
        committed = subprocess.run(command + ["after"], capture_output=True, timeout=20)
        self.assertEqual(committed.returncode, 24, committed.stderr)
        self.assertEqual(self.store.snapshot().sequence, 1)
        retry = self.withdraw("interrupted", revision=ZERO)
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["read_seq"], 1)
        self.assertEqual(self.engine.brief("quartz")["results"], [])

    def test_retry_of_an_older_event_returns_the_current_consistent_head(self):
        self.enable()
        self.withdraw()
        latest = self.withdraw("w2")
        retried = self.withdraw(revision=ZERO)
        self.assertTrue(retried["replayed"])
        self.assertEqual((retried["read_seq"], retried["revision"]), (latest["read_seq"], latest["revision"]))
        self.assertEqual(self.store.snapshot().sequence, 2)

    def test_request_id_cannot_change_its_operation_identity(self):
        self.enable()
        self.withdraw()
        with self.assertRaisesRegex(ControlError, "identity conflicts"):
            self.withdraw(scope=".")
        with self.assertRaisesRegex(ControlError, "identity conflicts"):
            self.reinstate(request="w1")
        self.assertEqual(self.store.snapshot().sequence, 1)

    def test_retries_recheck_current_effectiveness(self):
        self.enable()
        self.withdraw()
        self.reinstate()
        with self.assertRaisesRegex(ControlError, "no longer effective"):
            self.withdraw(revision=ZERO)
        self.withdraw("w2")
        with self.assertRaisesRegex(ControlError, "no longer effective"):
            self.store.change("reinstate", "r1", ZERO, target="w1")

    def test_snapshot_precedes_withdrawal_but_next_query_sees_latest_ledger(self):
        self.enable()
        before = self.store.snapshot()
        original_refresh = self.engine.refresh

        def interleaved_refresh(*args, **kwargs):
            self.withdraw()
            return original_refresh(*args, **kwargs)

        with patch.object(self.engine, "refresh", side_effect=interleaved_refresh):
            old_query = self.engine.brief("quartz")
        self.assertTrue(old_query["results"])
        self.assertEqual(old_query["refresh"]["control"]["read_seq"], 0)
        self.assertFalse(before.denies(self.path, self.sha))
        self.assertEqual(self.engine.brief("quartz")["results"], [])
        self.assertTrue(self.store.snapshot().denies(self.path, self.sha))

    def test_expired_snapshot_is_rejected(self):
        self.enable()
        expired = replace(self.store.snapshot(), captured=time.monotonic() - 301)
        with patch.object(self.engine.controls, "snapshot", return_value=expired):
            with self.assertRaisesRegex(ControlError, "expired"):
                self.engine.read("team/a.md", 1, 2, 8192)

    def test_missing_required_ledger_is_not_recreated(self):
        self.enable()
        self.store.path.unlink()
        for operation in (self.store.snapshot, self.store.enable, lambda: self.engine.brief("quartz")):
            with self.assertRaisesRegex(ControlError, "missing"):
                operation()
        self.assertFalse(self.store.path.exists())

    def test_interrupted_initialization_is_fail_closed(self):
        self.store.directory.mkdir(parents=True)
        with self.assertRaisesRegex(ControlError, "missing"):
            self.store.enable()

    def test_unsupported_schema_and_damaged_database_fail_closed(self):
        self.enable()
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ControlError, "Unsupported"):
            self.store.snapshot()
        self.store.path.write_bytes(b"not a SQLite database")
        with self.assertRaises(ControlError):
            self.engine.brief("quartz")

    def test_chain_binds_event_identity_action_and_sequence(self):
        self.enable()
        self.withdraw()
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            event = json.loads(connection.execute("SELECT payload FROM events").fetchone()[0])
            event["id"] = "tampered"
            connection.execute("UPDATE events SET payload=?", (json.dumps(event),))
        with self.assertRaisesRegex(ControlError, "chain"):
            self.store.snapshot()

    def test_lost_tail_cannot_match_the_unchanged_head(self):
        self.enable()
        self.withdraw()
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("DELETE FROM events")
        with self.assertRaisesRegex(ControlError, "head"):
            self.store.snapshot()

    def test_backup_uses_a_consistent_sqlite_snapshot_including_wal(self):
        self.enable()
        with closing(sqlite3.connect(self.store.path)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            self.withdraw()
            destination = self.base / "backup.sqlite3"
            backup = self.store.backup(destination)
            self.assertEqual(backup["read_seq"], 1)
            self.assertEqual(backup["backup_sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
            with closing(sqlite3.connect(destination)) as copied:
                copied.row_factory = sqlite3.Row
                self.assertTrue(self.store._load(copied)[0].denies(self.path, self.sha))
        with self.assertRaises(FileExistsError):
            self.store.backup(destination)
        with self.assertRaises(ControlError):
            self.store.backup(self.store.directory / "other.sqlite3")

    def test_control_and_cache_directories_cannot_overlap(self):
        with self.assertRaises(ContextError):
            Engine(self.root, self.store.directory / "cache", home=self.home)
        with self.assertRaises(ContextError):
            Engine(self.root, self.store.directory.parent, home=self.home)

    def test_receipt_reset_does_not_remove_withdrawals(self):
        self.enable()
        self.withdraw()
        self.engine.forget("owner")
        self.assertEqual(self.store.snapshot().sequence, 1)
        self.assertEqual(self.engine.brief("quartz")["results"], [])

    def test_cli_prepare_and_control_checks_share_the_installed_control_home(self):
        onboard(SOURCE, self.home)
        self.enable()
        self.withdraw()
        command = [
            sys.executable, "-I", "-B", str(self.home / ".context-slice" / "run.py"),
        ]
        common = ["--root", str(self.root), "--state-dir", str(self.base / "cli-cache")]
        result = subprocess.run(
            command + ["prepare", "quartz", "--session", "s1", "--max-bytes", "2048"] + common,
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(packet["results"], [])
        self.assertTrue(packet["activation"]["refresh_required"])
        self.assertEqual(len(result.stdout), packet["budget"]["returned_bytes"])
        self.assertLessEqual(len(result.stdout), 2048)
        result = subprocess.run(
            command + ["control-check", "team/a.md", "--sha256", self.sha] + common,
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["allowed"])
        result = subprocess.run(
            command + ["control-check", "team/a.md", "--sha256", self.sha.upper(),
                       "--root", str(self.root), "--state-dir", str(self.base / "unused-cache")],
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.base / "unused-cache").exists())
        result = subprocess.run(
            command + ["session-status", "--session", "s1"], capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            command + ["brief", "quartz", "--home", str(self.base / "other-home")] + common,
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot switch", json.loads(result.stderr)["message"])

    def test_failed_control_check_does_not_emit_a_source_body(self):
        onboard(SOURCE, self.home)
        self.enable()
        self.store.path.unlink()
        result = subprocess.run(
            [sys.executable, "-I", str(self.home / ".context-slice" / "run.py"),
             "read", "team/a.md", "--root", str(self.root), "--state-dir", str(self.base / "cli-cache")],
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(result.stdout)
        self.assertNotIn(b"quartz retained source", result.stderr)
        self.assertIn("missing", json.loads(result.stderr)["message"])
