from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .engine import ContextError, Engine, document_path, document_root, finish, wire
from .controls import ControlError, ControlStore, HASH


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Local, incremental, byte-budgeted context retrieval.")
    main.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, required=True, help="Explicitly authorized local document root")
    common.add_argument("--state-dir", type=Path, help="Private cache outside corpora and Git worktrees")
    common.add_argument("--home", type=Path, help="Managed account home; the installed launcher binds this value")
    commands = main.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check Python/SQLite capability without reading a corpus")
    index = commands.add_parser("index", parents=[common], help="Refresh only changed documents")
    index.add_argument("--scope", default="", help="Optional root-relative directory or file")
    index.add_argument("--verify", action="store_true", help="Rehash all in-scope files, not just changed metadata")
    brief = commands.add_parser("brief", parents=[common], help="Search and return a bounded evidence packet")
    brief.add_argument("query")
    brief.add_argument("--scope", default="")
    brief.add_argument("--limit", type=int, default=3)
    brief.add_argument("--max-bytes", type=int, default=8192)
    brief.add_argument("--session", default="", help="Reuse only explicitly acknowledged excerpts")
    prepare = commands.add_parser("prepare", parents=[common], help="Bounded retrieval plus an explicit session-use receipt")
    prepare.add_argument("query")
    prepare.add_argument("--scope", default="")
    prepare.add_argument("--limit", type=int, default=3)
    prepare.add_argument("--max-bytes", type=int, default=8192)
    prepare.add_argument("--session", required=True)
    prepare.add_argument("--ack-instructions", default="", help="Current instruction SHA-256, only after actually reading it")
    session_status = commands.add_parser("session-status", help="Read-only installed-runtime and session-receipt status")
    session_status.add_argument("--session", required=True)
    session_status.add_argument("--home", type=Path)
    read = commands.add_parser("read", parents=[common], help="Read an exact source range")
    read.add_argument("path")
    read.add_argument("--start", type=int, default=1)
    read.add_argument("--end", type=int, default=80)
    read.add_argument("--sha256", default="")
    read.add_argument("--max-bytes", type=int, default=8192)
    outline = commands.add_parser("outline", parents=[common], help="Show source headings and line addresses")
    outline.add_argument("path")
    outline.add_argument("--max-bytes", type=int, default=8192)
    ack = commands.add_parser("ack", parents=[common], help="Acknowledge a packet only after reading it")
    ack.add_argument("--session", required=True)
    ack.add_argument("--delivery-id", required=True)
    forget = commands.add_parser("forget", parents=[common], help="Reset receipts after context loss/compaction")
    forget.add_argument("--session", required=True)
    for command, help_text in (
        ("control-enable", "Explicitly enable durable account-local withdrawal controls"),
        ("control-status", "Read current control revision without creating a cache"),
        ("control-backup", "Create a consistent private SQLite backup, never an older-state restore"),
        ("control-check", "Check an exact source hash against current withdrawal controls"),
    ):
        command_parser = commands.add_parser(command, parents=[common], help=help_text)
        if command == "control-backup":
            command_parser.add_argument("--output", type=Path, required=True)
        if command == "control-check":
            command_parser.add_argument("path")
            command_parser.add_argument("--sha256", required=True)
    withdraw = commands.add_parser("withdraw", parents=[common], help="Withdraw exact source bytes in an explicit scope")
    withdraw.add_argument("path")
    withdraw.add_argument("--sha256", required=True)
    withdraw.add_argument("--scope", required=True, help="Explicit file/directory scope; '.' selects the authorized root")
    withdraw.add_argument("--request-id", required=True)
    withdraw.add_argument("--expected-revision", required=True)
    reinstate = commands.add_parser("reinstate", parents=[common], help="Remove only one explicitly referenced withdrawal")
    reinstate.add_argument("--withdrawal-id", required=True)
    reinstate.add_argument("--request-id", required=True)
    reinstate.add_argument("--expected-revision", required=True)
    return main


def main(argv: list[str] | None = None, *, home: Path | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        requested_home = getattr(args, "home", None)
        if home is not None and requested_home is not None and home.resolve() != requested_home.expanduser().resolve():
            raise ContextError("The managed launcher cannot switch its account/control home.")
        args.home = home or requested_home or Path.home()
        if args.command == "doctor":
            from .onboarding import OnboardError, doctor

            try:
                report = doctor()
            except OnboardError as error:
                raise ContextError(str(error)) from error
            sys.stdout.buffer.write(wire(report))
            return 0
        if args.command == "session-status":
            from .session import session_state

            sys.stdout.buffer.write(wire(session_state(args.home, args.session)[0]))
            return 0
        if args.command in {"control-enable", "control-status", "control-backup", "control-check", "reinstate"}:
            controls = ControlStore(args.home)
            root = document_root(args.root)
            if args.command == "control-enable":
                if controls.directory.resolve().is_relative_to(root):
                    raise ContextError("Durable controls must remain outside the source corpus.")
                result = controls.enable()
            elif args.command == "control-status":
                result = controls.snapshot().report()
            elif args.command == "control-backup":
                if args.output.expanduser().resolve().is_relative_to(root):
                    raise ContextError("Private control backups must remain outside the source corpus.")
                result = controls.backup(args.output)
            elif args.command == "control-check":
                sha = args.sha256.lower()
                if not HASH.fullmatch(sha):
                    raise ContextError("Provide an exact source SHA-256.")
                path = document_path(root, args.path)
                if not path.is_file():
                    raise ContextError("A control check requires one regular source file.")
                snapshot = controls.snapshot()
                result = {
                    **snapshot.report(), "allowed": not snapshot.denies(path, sha),
                    "meaning": "Withdrawal check only; not truth, source freshness, or general authorization.",
                }
            else:
                result = controls.change(
                    "reinstate", args.request_id, args.expected_revision, target=args.withdrawal_id,
                )
            sys.stdout.buffer.write(wire(finish(result, 8192)))
            return 0
        if args.command == "prepare":
            from .session import session_state

            if not 2048 <= args.max_bytes <= 262144:
                raise ContextError("Prepare requires a 2048-262144 byte budget.")
            activation = session_state(args.home, args.session)[0]
            if args.ack_instructions and args.ack_instructions.lower() != activation["instructions"]:
                raise ContextError("Instruction acknowledgement does not match the current instruction hash.")
        with Engine(args.root, args.state_dir, home=args.home) as engine:
            match args.command:
                case "index":
                    result = finish({"refresh": engine.refresh(args.scope, verify=args.verify)}, 8192)
                case "brief":
                    result = engine.brief(
                        args.query, scope=args.scope, limit=args.limit,
                        max_bytes=args.max_bytes, session=args.session,
                    )
                case "prepare":
                    from .session import record_use

                    reserve = len(wire(activation)) + 192
                    result = engine.brief(
                        args.query, scope=args.scope, limit=args.limit,
                        max_bytes=args.max_bytes - reserve, session=args.session,
                    )
                    result["activation"] = record_use(args.home, args.session, args.ack_instructions)
                    result = finish(result, args.max_bytes)
                case "read":
                    result = engine.read(args.path, args.start, args.end, args.max_bytes, args.sha256)
                case "outline":
                    result = engine.outline(args.path, args.max_bytes)
                case "ack":
                    result = finish(engine.acknowledge(args.session, args.delivery_id), 8192)
                case "forget":
                    result = finish(engine.forget(args.session), 8192)
                case "withdraw":
                    result = finish(engine.withdraw(
                        args.path, args.sha256, args.scope, args.request_id, args.expected_revision,
                    ), 8192)
                case _:
                    raise ContextError("Unknown operation.")
        sys.stdout.buffer.write(wire(result))
        return 0
    except (ContextError, ControlError, OSError, sqlite3.Error, ValueError, TypeError) as error:
        message = str(error)
        if isinstance(error, sqlite3.Error):
            message = f"SQLite error: {message}. Existing state was not silently reset."
        sys.stderr.buffer.write(wire({"error": type(error).__name__, "message": message[:800]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
