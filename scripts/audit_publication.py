"""Bounded publication hygiene checks; not a complete secret detector."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], timeout=30)


def main() -> int:
    findings = []
    commits = git("log", "--all", "--format=%H%x00%ae%x00%ce").decode().splitlines()
    for record in commits:
        revision, author, committer = record.split("\0")
        for field, email in (("author", author), ("committer", committer)):
            if not email.casefold().endswith("@users.noreply.github.com"):
                findings.append({"kind": "non_private_git_email", "commit": revision, "field": field})
    patterns = {
        "credential_pattern": re.compile(
            rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16}|"
            rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
        ),
        "literal_user_home": re.compile(rb"(?:[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+)"),
    }
    count = 0
    for record in git("rev-list", "--objects", "--all").decode().splitlines():
        oid, _, path = record.partition(" ")
        if git("cat-file", "-t", oid).strip() != b"blob":
            continue
        count += 1
        content = git("cat-file", "blob", oid)
        for kind, pattern in patterns.items():
            if pattern.search(content):
                findings.append({"kind": kind, "blob": oid, "path": path})
        if Path(path).suffix in (".sqlite3", ".db", ".env"):
            findings.append({"kind": "runtime_state_or_environment_file", "blob": oid, "path": path})
    print(json.dumps({"commits": len(commits), "blobs": count, "findings": findings}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
