"""Explicit session-use receipts, distinct from instruction or excerpt acknowledgement."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from . import __version__
from .engine import ContextError
from .locking import file_lock
from .onboarding import OnboardError, atomic_write, desired_release, existing_bytes, inspect_install
from .runtime import RuntimeIntegrityError, canonical, sha256


def session_state(home: Path, session: str) -> tuple[dict, Path, bytes | None]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", session):
        raise ContextError("Use a nonempty session identifier containing letters, numbers, '.', '_', ':', or '-'.")
    home = home.expanduser().resolve()
    root = home / ".context-slice"
    instruction = home / ".copilot" / "instructions" / "context-slice.instructions.md"
    try:
        installed, owned = inspect_install(root, instruction)
        manifest, _, _ = desired_release(Path(__file__).resolve().parents[1])
        fingerprint = sha256(canonical(manifest))
        if installed is None or installed["release"] != fingerprint or any(value is None for value in owned.values()):
            raise ContextError("The running source is not the intact installed release; run the reviewed onboarding first.")
        path = root / "sessions" / (sha256(session.encode("utf-8")) + ".json")
        raw = existing_bytes(path)
        previous = json.loads(raw) if raw is not None else None
        if previous is not None and (
            not isinstance(previous, dict) or previous.get("schema") != 1
            or type(previous.get("uses")) is not int or not 0 <= previous["uses"] < 2**63
            or type(previous.get("instructions_acknowledged")) is not bool
            or any(not isinstance(previous.get(key), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", previous[key])
                   for key in ("runtime", "instructions"))
        ):
            raise ContextError("Invalid existing session receipt; it was not reset.")
        current = previous is not None and (
            previous.get("runtime") == fingerprint
            and previous.get("instructions") == installed["instruction_sha256"]
        )
        result = {
            "schema": 1, "version": __version__,
            "session_key": path.stem,
            "runtime": fingerprint,
            "instructions": installed["instruction_sha256"],
            "runtime_verified": True,
            "instructions_acknowledged": current and previous["instructions_acknowledged"],
            "uses": previous["uses"] if current else 0,
            "recorded": current,
            "refresh_required": not (current and previous["instructions_acknowledged"]),
        }
        return result, path, raw
    except (OnboardError, RuntimeIntegrityError, ValueError, TypeError) as error:
        raise ContextError(str(error)) from error


def record_use(home: Path, session: str, acknowledge: str = "") -> dict:
    root = home.expanduser().resolve() / ".context-slice"
    with file_lock(root / "session-activation.lock"):
        result, path, previous = session_state(home, session)
        if acknowledge:
            if acknowledge.lower() != result["instructions"]:
                raise ContextError("Instruction acknowledgement does not match the current instruction hash.")
            result["instructions_acknowledged"] = True
        result["refresh_required"] = not result["instructions_acknowledged"]
        result.update(
            uses=result["uses"] + 1, recorded=True,
            last_used_at=datetime.now(timezone.utc).isoformat(),
        )
        atomic_write(path, canonical(result), previous)
        return result
