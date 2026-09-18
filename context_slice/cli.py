from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .engine import ContextError, Engine, finish, wire


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Local, incremental, byte-budgeted context retrieval.")
    main.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, required=True, help="Explicitly authorized local document root")
    common.add_argument("--state-dir", type=Path, help="Private cache outside corpora and Git worktrees")
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
    prepare.add_argument("--home", type=Path, default=Path.home())
    prepare.add_argument("--ack-instructions", default="", help="Current instruction SHA-256, only after actually reading it")
    session_status = commands.add_parser("session-status", help="Read-only installed-runtime and session-receipt status")
    session_status.add_argument("--session", required=True)
    session_status.add_argument("--home", type=Path, default=Path.home())
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
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
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
        if args.command == "prepare":
            from .session import session_state

            if not 2048 <= args.max_bytes <= 262144:
                raise ContextError("Prepare requires a 2048-262144 byte budget.")
            activation = session_state(args.home, args.session)[0]
            if args.ack_instructions and args.ack_instructions.lower() != activation["instructions"]:
                raise ContextError("Instruction acknowledgement does not match the current instruction hash.")
        with Engine(args.root, args.state_dir) as engine:
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
                case _:
                    raise ContextError("Unknown operation.")
        sys.stdout.buffer.write(wire(result))
        return 0
    except (ContextError, OSError, sqlite3.Error) as error:
        message = str(error)
        if isinstance(error, sqlite3.Error):
            message = f"Index error: {message}. Existing state was not silently reset."
        sys.stderr.buffer.write(wire({"error": type(error).__name__, "message": message[:800]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
