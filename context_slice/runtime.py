"""Stable, dependency-free launcher copied into the user's managed install."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import sys


class RuntimeIntegrityError(Exception):
    pass


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reject_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise RuntimeIntegrityError("Managed runtime paths cannot be symbolic links or reparse points.")


def release_path(root: Path, pointer: dict) -> Path:
    if pointer.get("schema") not in (1, 2):
        raise RuntimeIntegrityError("Unsupported managed installation schema.")
    fingerprint = pointer.get("release", "")
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise RuntimeIntegrityError("Invalid release fingerprint.")
    for path in (root, root / "releases", root / "releases" / fingerprint):
        reject_link(path)
    release = root / "releases" / fingerprint
    manifest_path = release / "manifest.json"
    reject_link(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    if sha256(manifest_bytes) != fingerprint:
        raise RuntimeIntegrityError("Release manifest integrity check failed.")
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != 1 or manifest.get("version") != pointer.get("version"):
        raise RuntimeIntegrityError("Release version or schema does not match the installation.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeIntegrityError("The release file manifest is missing.")
    for relative, expected in files.items():
        if not re.fullmatch(r"context_slice/[a-z_]+\.py", relative):
            raise RuntimeIntegrityError("The release contains an unexpected runtime path.")
        path = release / relative
        reject_link(path.parent)
        reject_link(path)
        if sha256(path.read_bytes()) != expected:
            raise RuntimeIntegrityError("A managed runtime file has changed; refusing to execute it.")
    actual = {p.relative_to(release).as_posix() for p in (release / "context_slice").glob("*.py")}
    if actual != set(files):
        raise RuntimeIntegrityError("The runtime contains unmanifested Python modules.")
    return release


def main() -> int:
    try:
        root = Path(__file__).absolute().parent
        reject_link(Path(__file__).absolute())
        reject_link(root)
        root = root.resolve()
        reject_link(root / "current.json")
        pointer = json.loads((root / "current.json").read_bytes())
        if sha256(Path(__file__).read_bytes()) != pointer.get("launcher_sha256"):
            raise RuntimeIntegrityError("The managed launcher differs from its installation receipt.")
        release = release_path(root, pointer)
        if (root / "controls").exists() and not (release / "context_slice" / "controls.py").is_file():
            raise RuntimeIntegrityError("Enabled withdrawal controls require a control-aware release.")
        sys.path.insert(0, str(release))
        from context_slice.cli import main as cli_main

        return cli_main(home=root.parent)
    except (OSError, ValueError, TypeError, RuntimeIntegrityError) as error:
        sys.stderr.write(json.dumps({"error": type(error).__name__, "message": str(error)[:600]}) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
