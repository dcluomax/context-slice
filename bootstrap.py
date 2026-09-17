"""Ensure an explicitly pinned public release; unchanged installs use no network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


REPOSITORY = "https://github.com/dcluomax/context-slice.git"


class BootstrapError(Exception):
    pass


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path: Path) -> bytes:
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise BootstrapError("Bootstrap paths cannot be links or reparse points.")
    if not path.is_file():
        raise BootstrapError("A required bootstrap artifact is not a regular file.")
    return path.read_bytes()


def read_pin(path: Path) -> dict:
    pin = json.loads(regular_bytes(path))
    if (
        not isinstance(pin, dict) or pin.get("schema") != 1
        or pin.get("repository") != REPOSITORY
        or not re.fullmatch(r"[a-f0-9]{40}", pin.get("revision", ""))
        or not re.fullmatch(r"[a-f0-9]{64}", pin.get("fingerprint", ""))
        or not re.fullmatch(r"\d+\.\d+\.\d+", pin.get("version", ""))
    ):
        raise BootstrapError("Use a reviewed schema-1 pin with this repository, full commit, fingerprint, and version.")
    return pin


def installed_matches(home: Path, pin: dict) -> bool:
    root = home / ".context-slice"
    current = root / "current.json"
    if not current.exists():
        return False
    pointer = json.loads(regular_bytes(current))
    if not isinstance(pointer, dict) or pointer.get("schema") != 1:
        raise BootstrapError("Unsupported installation receipt; it was not reset.")
    if pointer.get("release") != pin["fingerprint"] or pointer.get("version") != pin["version"]:
        return False
    release = root / "releases" / pin["fingerprint"]
    manifest_bytes = regular_bytes(release / "manifest.json")
    if digest(manifest_bytes) != pin["fingerprint"]:
        raise BootstrapError("The installed manifest differs from the approved release.")
    manifest = json.loads(manifest_bytes)
    files = manifest.get("files", {})
    if not files or any(not re.fullmatch(r"context_slice/[a-z_]+\.py", key) for key in files):
        raise BootstrapError("Invalid installed file manifest.")
    for relative, expected in files.items():
        if digest(regular_bytes(release / relative)) != expected:
            raise BootstrapError("An installed runtime file changed; manual repair is required.")
    if {p.relative_to(release).as_posix() for p in (release / "context_slice").glob("*.py")} != set(files):
        raise BootstrapError("Unexpected Python modules exist in the managed release.")
    launcher = root / "run.py"
    instruction = home / ".copilot" / "instructions" / "context-slice.instructions.md"
    if not launcher.exists() or not instruction.exists():
        return False
    if digest(regular_bytes(launcher)) != files.get("context_slice/runtime.py"):
        raise BootstrapError("The installed launcher is not the approved runtime launcher.")
    if digest(regular_bytes(instruction)) != pointer.get("instruction_sha256"):
        raise BootstrapError("The user instruction was modified; it will not be overwritten.")
    return True


def fetch_source(destination: Path, pin: dict) -> None:
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    commands = (
        ["git", "init", "--quiet", str(destination)],
        ["git", "-C", str(destination), "remote", "add", "origin", REPOSITORY],
        ["git", "-C", str(destination), "-c", "protocol.file.allow=never",
         "fetch", "--quiet", "--depth=1", "origin", pin["revision"]],
        ["git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    )
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=120, env=environment)
    actual = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], env=environment, timeout=15,
    ).decode("ascii").strip()
    if actual != pin["revision"]:
        raise BootstrapError("Fetched Git revision does not match the reviewed pin.")


def verify_source(source: Path, pin: dict) -> dict[str, bytes]:
    files = {
        "context_slice/" + path.name: regular_bytes(path).replace(b"\r\n", b"\n")
        for path in sorted((source / "context_slice").glob("*.py"))
    }
    if not files or any(not re.fullmatch(r"context_slice/[a-z_]+\.py", path) for path in files):
        raise BootstrapError("The fetched package has an unsupported module layout.")
    manifest = {
        "schema": 1, "version": pin["version"],
        "files": {path: digest(data) for path, data in files.items()},
    }
    if digest(canonical(manifest)) != pin["fingerprint"]:
        raise BootstrapError("Source fingerprint mismatch; no downloaded Python code was executed.")
    return files


def ensure(home: Path, pin: dict, *, check: bool = False) -> dict:
    home = home.expanduser().resolve()
    current = installed_matches(home, pin)
    result = {
        "schema": 1, "up_to_date": current, "changed": False,
        "version": pin["version"], "release": pin["fingerprint"],
        "network_fetches": 0,
    }
    if current or check:
        return result
    if shutil.which("git") is None:
        raise BootstrapError("Git is needed only for first install or a pinned upgrade.")
    with tempfile.TemporaryDirectory(prefix="context-slice-bootstrap-") as directory:
        root = Path(directory).resolve()
        source, verified = root / "source", root / "verified"
        fetch_source(source, pin)
        files = verify_source(source, pin)
        (verified / "context_slice").mkdir(parents=True)
        for relative, content in files.items():
            (verified / relative).write_bytes(content)
        command = [
            sys.executable, "-I", "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "from context_slice.onboarding import main;"
            "raise SystemExit(main(['--home',sys.argv[2]]))",
            str(verified), str(home),
        ]
        completed = subprocess.run(command, check=True, timeout=60, capture_output=True)
        installed = json.loads(completed.stdout)
        if not installed.get("up_to_date") or not installed_matches(home, pin):
            raise BootstrapError("Installer returned without establishing the approved runtime.")
    result.update(up_to_date=True, changed=installed["changed"], network_fetches=1)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--check", action="store_true", help="Read-only; never download or install")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 11):
            raise BootstrapError("Python 3.11 or newer is required.")
        result = ensure(args.home, read_pin(args.release_file.resolve()), check=args.check)
        sys.stdout.buffer.write(canonical(result))
        return 0 if result["up_to_date"] else 3
    except (OSError, ValueError, TypeError, BootstrapError, subprocess.SubprocessError) as error:
        message = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            message = "Bootstrap command failed. " + (error.stderr or b"").decode("utf-8", errors="replace")[:400]
        sys.stderr.buffer.write(canonical({"error": type(error).__name__, "message": message[:700]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
