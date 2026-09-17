"""Live public-fetch smoke test using a disposable, synthetic user home."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile


SOURCE = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="context-slice-live-onboard-") as directory:
        home = Path(directory).resolve() / "synthetic-home"
        command = [
            sys.executable, str(SOURCE / "bootstrap.py"),
            "--release-file", str(SOURCE / "release.json"), "--home", str(home),
        ]
        first = subprocess.run(command, check=True, capture_output=True, timeout=180)
        second = subprocess.run(command, check=True, capture_output=True, timeout=30)
        first_result, second_result = json.loads(first.stdout), json.loads(second.stdout)
        assert first_result["up_to_date"] and first_result["network_fetches"] == 1
        assert second_result["up_to_date"] and second_result["network_fetches"] == 0
        assert not second_result["changed"]
        doctor = subprocess.run(
            [sys.executable, "-I", str(home / ".context-slice" / "run.py"), "doctor"],
            check=True, capture_output=True, timeout=20,
        )
        assert json.loads(doctor.stdout)["fts5"]
        print(json.dumps({
            "public_bootstrap": True, "second_entry_network_fetches": 0,
            "isolated_runtime": True, "version": first_result["version"],
        }))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.stderr.buffer.write(error.stderr or b"")
        raise
