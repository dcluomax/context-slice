# Local withdrawal controls

## Deliberately bounded guarantee

This opt-in feature stops controlled Context Slice operations from serving an
explicitly withdrawn **whole-file byte revision within an explicit path scope**.
It preserves source files, ordinary search behavior, read receipts and other
applications' configuration. It is not a second memory/claim store.

Rules use SHA-256 of exact source bytes, without Unicode or newline
normalization. An unchanged file restored from a content backup, or an exact
copy subsequently created within the scope, stays blocked. A paraphrase,
changed newline encoding, partial quotation, moved copy outside the scope,
or independently generated summary is not an exact-byte match. Do not describe
this as universal anti-relearning or partial-claim deletion.

Scope is an absolute canonical source location on this machine. Searches using
an enclosing or nested document root still consult the same account-level
controls. Case-only aliases are checked against the native filesystem, including
case-insensitive macOS volumes, without conflating distinct case-sensitive
directories. Hard-link/mount aliases and hostile same-account path manipulation
are not an OS isolation guarantee.

The supported boundary is the current managed launcher and cooperating current
library clients. Ordinary filesystem access, old standalone programs, other OS
accounts/machines, exports and already-delivered model contexts remain outside
it. In particular, this feature does not silently change an application's
Dreaming, transcript ingestion, sharing, retention or permission behavior.

## Commands

Use the installed `run.py` under the current user's home. The launcher binds
its control home; `--home` cannot redirect it to an empty policy namespace.
Source-checkout/library test clients can explicitly choose isolated homes.

```powershell
python "$HOME\.context-slice\run.py" control-enable --root C:\Notes
python "$HOME\.context-slice\run.py" control-status --root C:\Notes

# Supply the actual file hash and revision returned by control-status.
python "$HOME\.context-slice\run.py" withdraw team\example.md --root C:\Notes `
    --sha256 FILE_SHA256 --scope team --request-id OPAQUE_UNIQUE_ID `
    --expected-revision CONTROL_REVISION

# This is a withdrawal check, not an assertion of truth or general authorization.
python "$HOME\.context-slice\run.py" control-check team\example.md --root C:\Notes `
    --sha256 FILE_SHA256

# Restore only this referenced withdrawal. Other matching withdrawals remain.
python "$HOME\.context-slice\run.py" reinstate --root C:\Notes `
    --withdrawal-id ORIGINAL_REQUEST_ID --request-id ANOTHER_UNIQUE_ID `
    --expected-revision CURRENT_CONTROL_REVISION
```

Choose the narrowest authorized `--scope`; `.` explicitly selects the whole
authorized root. Re-read current controls after a compare-and-swap conflict.
Do not silently retry against a new revision on the user's behalf.

`control-check` checks the supplied revision against withdrawal rules, not
whether those bytes are currently on disk. Claim/readiness tools must separately
verify their exact source hash and repeat the control check before reporting
readiness. A readiness report is not a write permit.

`forget` only resets a session's excerpt receipts. It cannot withdraw or
reinstate content. No command automatically disables or resets the ledger.

## Authority and commit boundary

The account's `~/.context-slice/controls/ledger.sqlite3` is durable authority,
outside the corpus, Git and disposable search caches. Its directory is the
required-state marker: an existing directory with a missing ledger is an error,
including an interrupted initialization. Empty, malformed, unsupported,
oversized or inconsistent required state is never recreated automatically.

The bounded append-only event log stores opaque request IDs, action, scope,
source hash, referenced withdrawal and a UTC recording time; **no source body,
query text, free-text reason or generated summary**. Sequence, complete payload,
store identity and previous digest are bound by an integrity chain. The head and
event insertion share one SQLite transaction with `synchronous=FULL`.
No separate file anchor is acknowledged as if it were atomically committed.

Mutations use `BEGIN IMMEDIATE`, then validate existing state, the full
idempotency identity, the current expected revision and the target refusal
before inserting. Replays do not create another event; they return a currently
validated head, not an old sequence paired with a new hash. A superseded
operation cannot be retried as though it still authorized its previous effect.
Reinstatement never removes a separate active withdrawal.

There is no corpus-writing transaction here: controls never replace, delete,
roll back or automatically adopt a Markdown file. Therefore recovery cannot
overwrite a user's intervening edit. Ordinary content-writing tools still need
their own ownership and expected-source checks.

## Query visibility

At each operation's start, the implementation takes a validated snapshot of the
**authoritative ledger**, not the index's potentially stale watermark. Nested
index/source reads use that snapshot. A mutation's returned `read_seq` is its
effective sequence. Every subsequently started operation sees that commit or a
later one, regardless of whether the index has been rebuilt.

An already-started operation may complete using its older snapshot; withdrawal
does not retract an in-flight or already-emitted packet. Snapshots expire after
300 monotonic seconds, and an expired operation is rejected. Callers cannot
choose an older sequence. Source hashes and existing race detection remain
independent of the control snapshot; this is not a filesystem-wide snapshot.

## Backup, upgrade and limits

```powershell
# The destination directory must already exist and be privately protected.
python "$HOME\.context-slice\run.py" control-backup --root C:\Notes `
    --output C:\PrivateBackups\withdrawals-unique.sqlite3
```

Backup uses SQLite's backup API and validates the resulting chain. The
destination must be new, outside the corpus, Git and the online control
directory. A failed backup can leave an incomplete destination: do not restore
it or treat its existence as success. The returned SHA-256 proves snapshot
integrity, **not latestness**. Never copy an open WAL database as a backup.

Keep controls separate when rebuilding caches or upgrading code. The current
launcher refuses a selected runtime without control support while controls are
enabled. Version 0.4 writes an installation pointer with schema 2; the previous
schema-1-only managed launcher and installer reject it instead of silently
downgrading. New code accepts an intact schema-1 installation during upgrade.
This cannot add a gate to an unrelated old standalone executable: stop using
those before enabling withdrawals. Never restore an older pointer or ledger
to bypass this boundary, or remove the required-state directory to clear an error.

Repair requires the latest independently verified control state. There is no
automatic restore command, tail fast-forward, multi-host synchronization,
remote permission subscription or externally anchored high-water mark. A
partial event-tail loss with an unchanged head is detectable; replacing the
entire ledger with an internally valid older snapshot is not. Deleting the
entire control directory, rolling back the machine or losing all its backups is
likewise outside the local guarantee. Block service when latest state cannot
be established; do not silently initialize a replacement.

SQLite commit and abrupt child-process exit before commit and after commit
(without a caller receipt) are exercised with synthetic temporary data. Native
CI also exercises each OS's existing locking primitives.
These tests are not proof of power-loss persistence, hostile-process isolation,
distinct-account ACL enforcement or physical-host deployment.
