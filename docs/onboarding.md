# Cross-platform onboarding

Context Slice supports Windows, macOS, and Linux with Python 3.11+ and SQLite
FTS5. Onboarding uses only the standard library. It does not require pip, a
virtual environment, Homebrew, administrator privileges, or a modified PATH.

## Install for one user

Clone and review a published revision, then run the installer from that checkout:

```powershell
git clone https://github.com/dcluomax/context-slice.git
cd context-slice
python onboard.py
```

On macOS/Linux, normally use `python3` instead of `python`. If `python3` is
older than 3.11, explicitly select a supported interpreter, such as `python3.12`.
Missing Python or SQLite FTS5 is an explicit prerequisite failure, not permission
to change a machine's package manager automatically.

The installer writes only to the chosen user's home:

| Artifact | Purpose |
| --- | --- |
| `~/.context-slice/releases/<fingerprint>` | Immutable, hash-verified runtime modules |
| `~/.context-slice/current.json` | Atomic active-release and ownership receipt |
| `~/.context-slice/run.py` | Stable launcher; does not depend on this checkout |
| `~/.context-slice/sessions/<session-hash>.json` | Explicit runtime-use and instruction-acknowledgement receipt |
| `~/.context-slice/controls/ledger.sqlite3` | Optional durable local withdrawal authority; never a disposable cache |
| `~/.copilot/instructions/context-slice.instructions.md` | User-level routing loaded by new Copilot CLI sessions |

The launcher path is stable on all operating systems. Quote it as one argument:

```powershell
python "$HOME\.context-slice\run.py" doctor
python "$HOME\.context-slice\run.py" brief "quartz rotation" --root C:\Notes
```

On macOS/Linux, use the same `run.py` under your home `.context-slice` directory
with the platform's path separator and `python3`. The installer prints the exact
path. It is independent of Python's user Scripts/bin directory.

`python onboard.py --check` is read-only. It returns code 0 only for an exact,
intact installation of this checkout, code 3 if onboarding is needed, and code 2
for a real error. Normal onboarding is idempotent and makes no network calls.

## All sessions and machines

User-level installation applies to new Copilot CLI sessions in any repository,
including sessions not started in a notes checkout. It is per OS account and per
machine: installing on one computer does not remotely change another.
Already-running sessions must reload their instructions or read updated
canonical policy. No installer can retroactively replace an existing model
context. Other agent clients can adopt `adapters/AGENTS.fragment.md`; their
configuration is not implicitly changed.

Every managed invocation selects the current installed release, so an existing
session's next tool call uses the activated code without restarting that
session. Activation receipts expose `refresh_required` until that session
actually acknowledges the current instruction after reading it. Do not
fabricate other sessions' acknowledgements or use a local receipt as fleet
deployment evidence.

Use Context Slice for applicable local text-document retrieval, not greetings,
already-supported answers, or evidence that requires another authoritative
source. Existing authorization, material-claim verification, full-implementation
reads, and canonical workflow controls remain in force.

At the first applicable lookup, `prepare` combines bounded retrieval with an
actual runtime-use receipt:

```powershell
python "$HOME\.context-slice\run.py" prepare "quartz rotation" --root C:\Notes --session example-session --max-bytes 8192
python "$HOME\.context-slice\run.py" session-status --session example-session
```

The second command is read-only. Installation, verified use, instruction
acknowledgement and document-read acknowledgement are different claims.
After reading the installed instruction file, pass its current SHA-256 through
`prepare --ack-instructions <hash>` to explicitly acknowledge that revision.
Neither command acknowledges retrieved excerpts or a knowledge base's revision.
A runtime or instruction change invalidates the corresponding current-session
activation. Receipts contain hashes and counters, not queries or document text.

For a knowledge base with a trusted bootstrap route, copy the reviewed standalone
`bootstrap.py` and the ready-to-use `release.json`. The included pin selects the
reviewed 0.3.0 runtime; later documentation-only commits do not change it.
Maintain its exact source and runtime binding when approving an upgrade:

```json
{
  "schema": 1,
  "repository": "https://github.com/dcluomax/context-slice.git",
  "revision": "<full reviewed 40-character commit>",
  "fingerprint": "<64-character runtime fingerprint from onboard --check>",
  "version": "0.3.0"
}
```

Run once at the first applicable session entry, not before every prompt:

```powershell
python bootstrap.py --release-file release.json
```

An intact matching install is verified locally without Git, network, indexing,
LLM calls, or rewriting instructions. Only a missing install or explicitly pinned
upgrade fetches the exact commit. The runtime fingerprint is checked before
any downloaded Python executes, and only verified package files enter the
installer's import path. An offline first install fails explicitly; an intact
existing install continues offline. `--check` never downloads anything.

Review each new pin. Do not track mutable `main`, accept a source-defined command,
or use unapproved retrieved content as a release manifest.

## Upgrade, conflicts, and recovery

Run onboarding from the new reviewed release. Old immutable releases, caches,
read receipts, other instructions, and user settings are retained. File hashes
bind managed ownership, so unowned or manually modified instructions/launchers
are never overwritten. Complete that manual conflict before retrying.

Installer operations use an OS file lock. Activation updates the active receipt
last and rolls back only its own file writes on a normal failure. A power loss
or conflicting external writer may require explicit reconciliation; it is never
reported as success or repaired by deleting unrelated files.

Version 0.3 uses a separate `index-v2.sqlite3`. An existing v1 index is copied and
migrated with its deliveries and read receipts; the original remains available
for rollback. Passage ranking separates prose from incidental local paths and
reports whether a result used all-term text, broad text, or location lookup.

Neither onboarding nor bootstrap edits the source corpus, uploads its contents,
enables synchronization, changes credentials, creates a daemon, or alters model
providers. Runtime and search-cache data remain outside Git repositories.

Version 0.4's [withdrawal controls](withdrawals.md) are explicitly opt-in;
installation alone neither enables them nor withdraws a source. Preserve their
durable directory across upgrades and cache rebuilds. Never roll back its state
with a code rollback, or use an old standalone client to bypass it.
Its schema-2 installation pointer makes previous schema-1-only managed
launchers/installers reject an unsupported downgrade. The new installer accepts
an intact schema-1 installation and preserves its receipts and control state.

Runtime fingerprints use canonical LF source bytes, not a platform's checkout
line endings. CRLF checkouts and LF checkouts produce the same immutable release.
The launcher still checks the exact bytes of the installed canonical files.
