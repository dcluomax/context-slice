"""Account-local withdrawal controls, separate from disposable retrieval caches."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from types import MappingProxyType
from typing import Mapping
import uuid

from .locking import file_lock
from .runtime import RuntimeIntegrityError, reject_link


class ControlError(Exception):
    """Invalid or unavailable controls must never become an empty allow list."""


class WithdrawnError(ControlError):
    pass


SCHEMA = 1
MAX_EVENTS = 10000
SNAPSHOT_SECONDS = 300
HASH = re.compile(r"[a-f0-9]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
ZERO = "0" * 64


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def fingerprint(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def location(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path.expanduser())).replace("\\", "/")


def within(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope.rstrip("/") + "/")


def in_scope(path: Path, scope: str) -> bool:
    candidate = location(path)
    if within(candidate, scope):
        return True
    if not within(candidate.casefold(), scope.casefold()):
        return False
    target = Path(scope)
    ancestor = Path(candidate)
    while len(ancestor.parts) > len(target.parts):
        ancestor = ancestor.parent
    try:
        return ancestor.samefile(target)
    except FileNotFoundError:
        return False


def checked_state(path: Path) -> None:
    try:
        for item in reversed((path, *path.parents)):
            if item.exists() or item.is_symlink():
                reject_link(item)
    except RuntimeIntegrityError as error:
        raise ControlError(str(error)) from error


@dataclass(frozen=True)
class Rule:
    request_id: str
    scope: str
    sha256: str


@dataclass(frozen=True)
class Snapshot:
    enabled: bool
    sequence: int
    revision: str
    rules: tuple[Rule, ...]
    captured: float
    _by_hash: Mapping[str, tuple[Rule, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        grouped: dict[str, list[Rule]] = {}
        for rule in self.rules:
            grouped.setdefault(rule.sha256, []).append(rule)
        object.__setattr__(self, "_by_hash", MappingProxyType({
            sha: tuple(rules) for sha, rules in grouped.items()
        }))

    def fresh(self) -> None:
        elapsed = time.monotonic() - self.captured
        if not 0 <= elapsed <= SNAPSHOT_SECONDS:
            raise ControlError("Control snapshot expired; start a new operation.")

    def denies(self, path: Path, sha256: str) -> bool:
        self.fresh()
        matching = self._by_hash.get(sha256, ())
        if not matching:
            return False
        return any(in_scope(path, rule.scope) for rule in matching)

    def report(self) -> dict:
        return {
            "enabled": self.enabled, "read_seq": self.sequence,
            "revision": self.revision, "active_withdrawals": len(self.rules),
        }


class ControlStore:
    def __init__(self, home: Path | None = None):
        self.directory = (home or Path.home()).expanduser().absolute() / ".context-slice" / "controls"
        self.path = self.directory / "ledger.sqlite3"

    def _exists(self) -> bool:
        checked_state(self.directory)
        if not self.directory.exists():
            return False
        if not self.directory.is_dir():
            raise ControlError("The required control directory is not a directory.")
        return True

    def _connect(self) -> sqlite3.Connection:
        checked_state(self.path)
        if not self.path.is_file() or not stat.S_ISREG(self.path.stat().st_mode):
            raise ControlError("Required withdrawal ledger is missing; repair it before retrieval.")
        if self.path.stat().st_size > 64 * 1024 * 1024:
            raise ControlError("Withdrawal ledger exceeds its size bound; explicit migration is required.")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    @staticmethod
    def _load(connection: sqlite3.Connection) -> tuple[Snapshot, list[dict]]:
        captured = time.monotonic()
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
            raise ControlError("Unsupported withdrawal schema; do not downgrade or reset the ledger.")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ControlError("Withdrawal ledger integrity check failed.")
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if set(metadata) != {"store_id", "head"} or not re.fullmatch(r"[a-f0-9]{32}", metadata["store_id"]):
            raise ControlError("Invalid withdrawal ledger metadata.")
        rows = connection.execute("SELECT seq,payload,previous,hash FROM events ORDER BY seq LIMIT ?", (MAX_EVENTS + 1,)).fetchall()
        if len(rows) > MAX_EVENTS:
            raise ControlError("Withdrawal ledger exceeds its supported bound; explicit migration is required.")
        previous, active, events, requests = ZERO, {}, [], set()
        for sequence, row in enumerate(rows, 1):
            try:
                event = json.loads(row["payload"])
            except (ValueError, TypeError) as error:
                raise ControlError("A withdrawal event is not valid JSON.") from error
            if (
                row["seq"] != sequence or row["previous"] != previous
                or not isinstance(event, dict)
                or set(event) != {"id", "action", "scope", "sha256", "target", "recorded_at"}
                or not all(isinstance(value, str) for value in event.values())
                or not IDENTIFIER.fullmatch(event["id"]) or event["id"] in requests
                or event["action"] not in {"withdraw", "reinstate"}
                or not HASH.fullmatch(event["sha256"])
                or not Path(event["scope"]).is_absolute()
                or len(event["scope"]) > 4096 or location(Path(event["scope"])) != event["scope"]
            ):
                raise ControlError("Invalid withdrawal event or sequence.")
            try:
                timestamp = datetime.fromisoformat(event["recorded_at"])
                if timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
                    raise ValueError("UTC required")
            except ValueError as error:
                raise ControlError("Invalid withdrawal event timestamp.") from error
            current = fingerprint([SCHEMA, metadata["store_id"], sequence, previous, event])
            if row["hash"] != current:
                raise ControlError("Withdrawal event chain validation failed.")
            if event["action"] == "withdraw":
                if event["target"]:
                    raise ControlError("A withdrawal cannot contain a reinstatement target.")
                active[event["id"]] = Rule(event["id"], event["scope"], event["sha256"])
            else:
                target = active.get(event["target"])
                if target is None or (target.scope, target.sha256) != (event["scope"], event["sha256"]):
                    raise ControlError("Invalid reinstatement reference.")
                del active[event["target"]]
            requests.add(event["id"])
            events.append(event)
            previous = current
        if metadata["head"] != previous:
            raise ControlError("Withdrawal ledger head does not match its committed events.")
        return Snapshot(True, len(events), previous, tuple(active.values()), captured), events

    def snapshot(self) -> Snapshot:
        if not self._exists():
            return Snapshot(False, 0, "disabled", (), time.monotonic())
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN")
                return self._load(connection)[0]
        except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
            raise ControlError("Withdrawal ledger cannot be validated; it was not reset.") from error

    def enable(self) -> dict:
        checked_state(self.directory)
        if any((parent / ".git").exists() for parent in (self.directory, *self.directory.parents)):
            raise ControlError("Durable controls must not be created inside a Git worktree.")
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.directory.parent / "control-activation.lock"):
            if self._exists():
                return self.snapshot().report()
            self.directory.mkdir(mode=0o700)
            # Directory presence is the fail-closed marker, including interrupted initialization.
            with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                connection.execute("""CREATE TABLE events(
                    seq INTEGER PRIMARY KEY,payload TEXT NOT NULL,
                    previous TEXT NOT NULL,hash TEXT NOT NULL UNIQUE)""")
                connection.executemany(
                    "INSERT INTO metadata VALUES (?,?)", (("store_id", uuid.uuid4().hex), ("head", ZERO)),
                )
                connection.execute(f"PRAGMA user_version={SCHEMA}")
            return self.snapshot().report()

    def change(
        self, action: str, request_id: str, expected_revision: str, *,
        scope: Path | None = None, sha256: str = "", target: str = "",
    ) -> dict:
        if not IDENTIFIER.fullmatch(request_id) or not HASH.fullmatch(expected_revision):
            raise ControlError("Use a bounded request ID and the current 64-character control revision.")
        if action not in {"withdraw", "reinstate"} or not self._exists():
            raise ControlError("Enable durable controls before submitting a supported control operation.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current, events = self._load(connection)
            if action == "withdraw":
                if scope is None or not HASH.fullmatch(sha256) or target:
                    raise ControlError("Withdrawal requires an explicit scope and exact SHA-256.")
                event = {"id": request_id, "action": action, "scope": location(scope), "sha256": sha256, "target": ""}
            else:
                original = next((item for item in events if item["id"] == target and item["action"] == "withdraw"), None)
                if original is None or scope is not None or sha256:
                    raise ControlError("Reinstatement must reference an existing withdrawal ID.")
                event = {
                    key: value for key, value in original.items() if key != "recorded_at"
                } | {"id": request_id, "action": action, "target": target}
            previous = next((item for item in events if item["id"] == request_id), None)
            active = {rule.request_id for rule in current.rules}
            if previous is not None:
                if {key: value for key, value in previous.items() if key != "recorded_at"} != event:
                    raise ControlError("Idempotency identity conflicts with the recorded operation.")
                positions = {item["id"]: index for index, item in enumerate(events)}
                if (
                    (action == "withdraw" and request_id not in active)
                    or (action == "reinstate" and any(
                        positions[rule.request_id] > positions[request_id]
                        and rule.sha256 == event["sha256"] and (
                            in_scope(Path(rule.scope), event["scope"])
                            or in_scope(Path(event["scope"]), rule.scope)
                        ) for rule in current.rules
                    ))
                ):
                    raise ControlError("The retried operation is no longer effective; inspect current controls.")
                return {**current.report(), "request_id": request_id, "replayed": True}
            if current.revision != expected_revision:
                raise ControlError("Control state changed before commit; inspect and retry with its current revision.")
            if action == "reinstate" and target not in active:
                raise ControlError("The referenced withdrawal is no longer active.")
            if current.sequence >= MAX_EVENTS:
                raise ControlError("Withdrawal ledger is full; explicit migration is required.")
            event["recorded_at"] = datetime.now(timezone.utc).isoformat()
            store_id = connection.execute("SELECT value FROM metadata WHERE key='store_id'").fetchone()[0]
            next_hash = fingerprint([SCHEMA, store_id, current.sequence + 1, current.revision, event])
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?)",
                (current.sequence + 1, encoded(event).decode("utf-8"), current.revision, next_hash),
            )
            connection.execute("UPDATE metadata SET value=? WHERE key='head'", (next_hash,))
            after, _ = self._load(connection)
            result = {**after.report(), "request_id": request_id, "replayed": False}
        return result

    def backup(self, destination: Path) -> dict:
        destination = destination.expanduser().absolute()
        checked_state(destination)
        if not self._exists():
            raise ControlError("No enabled withdrawal ledger to back up.")
        if destination.is_relative_to(self.directory):
            raise ControlError("Backups must not overwrite or inhabit the online control directory.")
        if any((parent / ".git").exists() for parent in (destination, *destination.parents)):
            raise ControlError("Private control backups must remain outside Git worktrees.")
        if not destination.parent.is_dir():
            raise ControlError("Create and authorize the backup directory first.")
        with file_lock(destination.parent / "control-backup.lock"):
            with destination.open("xb"):
                pass
            with closing(self._connect()) as source, closing(sqlite3.connect(destination)) as target:
                target.row_factory = sqlite3.Row
                target.execute("PRAGMA synchronous=FULL")
                source.backup(target)
                result, _ = self._load(target)
            return {
                **result.report(), "backup_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "freshness": "consistent snapshot, not proof of latestness or an external rollback anchor",
            }
