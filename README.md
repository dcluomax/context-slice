# Context Slice

Turn "read less context" advice into a local, measurable retrieval tool.

Inspired by the progressive-retrieval idea in
[AI Token Efficiency Playbook](https://github.com/ravinperera/ai-token-efficiency-playbook).
This is an independent implementation, not a fork or a copy of its code.

Context Slice targets large local Markdown knowledge bases. A Python script does
the repetitive discovery, indexing, selection, and byte accounting before an AI
assistant sees the result. It returns exact source excerpts, not AI summaries.

**No runtime dependencies, model calls, network service, account, or document
uploads.** Requires Python 3.11+ with SQLite FTS5.

## Onboard every session on Windows, macOS, and Linux

From a reviewed checkout, run:

```powershell
python onboard.py
```

Use `python3` on macOS/Linux when that is the supported interpreter. No pip,
administrator privileges, daemon, or PATH changes are required. This installs an
isolated, hash-verified runtime at `~/.context-slice` and a separate user-level
Copilot instruction file. New sessions in any repository then load the same
retrieval guidance; existing sessions must refresh their instructions.

The install is idempotent and refuses to overwrite unowned or manually modified
instructions. Other user configuration, corpus files, and caches are preserved.
For first-install automation and explicitly pinned upgrades, use the local-only
steady-state `bootstrap.py` workflow in [cross-platform onboarding](docs/onboarding.md).

## Quick start without onboarding

Run from this checkout; no installation is necessary:

```powershell
python .\context_slice_cli.py brief "quartz rotation" --root .\examples\notes
```

Or install the command in your user environment:

```powershell
python -m pip install --user .
context-slice brief "quartz rotation" --root .\examples\notes
```

If your user-level Scripts/bin directory is not on `PATH`, use
`python -m context_slice` instead of `context-slice`; all arguments are identical.

For a real local collection, provide its explicitly authorized root:

```powershell
context-slice brief "rotation rollback" --root C:\Notes --scope operations --max-bytes 8192
```

The first lookup builds the in-scope index. Later lookups enumerate metadata and
re-index changed documents, rather than reading every file body again. A narrow
`--scope` limits discovery as well as returned results.

## What is different from prompt-only advice?

| Advice | Enforced implementation |
| --- | --- |
| Search before reading everything | Persistent local SQLite FTS5 index; English stopwords and CJK bigrams |
| Load only useful evidence | Heading-aware chunks, best-matching line windows, source path/line/SHA-256 |
| Keep tool responses small | A hard UTF-8 byte limit including the complete JSON envelope and newline |
| Do not repeat unchanged context | Session receipts with explicit acknowledgement and source revalidation |
| Keep cached context fresh | Per-request metadata refresh, selected-file hashing, optional full rehash |
| Preserve omitted evidence | Counts, source pointers for budget omissions, and explicit range escalation |
| Keep explicitly withdrawn source revisions out of new packets | Opt-in durable controls, checked independently of the disposable index |

This is not a semantic code graph, an LLM proxy, a prompt compressor, or a
replacement for your agent's authorization and canonical-policy workflow.

## Progressive retrieval

Version 0.3 separates meaningful text from incidental machine paths. It tries all
query terms in text before a clearly labeled broad fallback, and reports
`path_lookup` when a filename/location is the useful match. Source excerpts
remain byte-exact. The v2 index is created separately; a v1 cache is copied and
migrated without modifying the original database or dropping read receipts.

```powershell
# Search first. The full JSON output is bounded, not just each snippet.
context-slice brief "quartz rotation" --root C:\Notes --limit 3 --max-bytes 8192

# Inspect structure, then retrieve exact inclusive lines when needed.
context-slice outline operations\maintenance.md --root C:\Notes
context-slice read operations\maintenance.md --root C:\Notes --start 7 --end 14

# Rehash every in-scope file after an external restore or preserved timestamps.
context-slice index --root C:\Notes --scope operations --verify
```

`read` accepts `--sha256` from an earlier packet and rejects a changed source.
It never silently truncates an oversized range. Increase the explicit budget or
choose a narrower range. `brief` reports `needs_read` pointers when a source
excerpt cannot fit.

The PowerShell adapter offers the same bounded lookup:

```powershell
.\scripts\Invoke-ContextSlice.ps1 -Root C:\Notes -Query "quartz rotation" -Scope operations
```

## Read receipts are opt-in

An emitted packet is not proof that the assistant read it:

```powershell
context-slice brief "quartz rotation" --root C:\Notes --session task-123
# Read the returned evidence first, then use the returned delivery_id:
context-slice ack --root C:\Notes --session task-123 --delivery-id RECEIPT_ID
context-slice brief "quartz rotation" --root C:\Notes --session task-123
```

The second acknowledged lookup returns source pointers in `already_read` rather
than re-sending that excerpt. A changed document is emitted again. A different
session cannot acknowledge your delivery. Unacknowledged deliveries expire after
one day; acknowledged receipts persist until reset.

**After compaction, a restarted conversation, or any loss of loaded context, use
a new session ID or run:**

```powershell
context-slice forget --root C:\Notes --session task-123
```

Do not share receipt IDs between agents as a substitute for transferring context.
These receipts do not acknowledge canonical policies, provider prompts, external
source snapshots, or a Git revision.

## Per-session activation and first lookup

After onboarding, combine the first applicable lookup and a use receipt:

```powershell
python "$HOME\.context-slice\run.py" prepare "quartz rotation" --root C:\Notes --session task-123
python "$HOME\.context-slice\run.py" session-status --session task-123
```

Use your actual current session ID. `prepare` verifies the installed runtime and
instruction file, performs bounded retrieval, and records successful use. Its
receipt contains hashes/counters, not queries, corpus paths, or document bodies.
This is **not proof that the model loaded or read its instructions**.
`instructions_acknowledged` remains false unless the caller explicitly supplies
`--ack-instructions` with the current hash after actually reading them. Changed
runtime/instruction hashes invalidate the current activation; other sessions
never inherit the acknowledgement. `session-status` is read-only.
The launcher selects the active installed release on every invocation, including
calls from an existing session. `refresh_required` explicitly identifies a
missing current instruction acknowledgement; installation never fabricates one.

The complete `prepare` response, including activation metadata, remains bounded.
Its minimum budget is 2,048 bytes. This activation receipt does not acknowledge
retrieval excerpts or canonical knowledge-policy revisions.

## Optional durable withdrawals

Version 0.4 adds **single-account, local, exact-byte withdrawal controls**.
Enabling the feature does not withdraw anything or change source files:

```powershell
python "$HOME\.context-slice\run.py" control-enable --root C:\Notes
python "$HOME\.context-slice\run.py" control-status --root C:\Notes
```

An explicitly requested `withdraw` binds the selected file's SHA-256, an explicit
file/directory scope, a unique request ID, and the current control revision.
`reinstate` references one withdrawal rather than clearing unrelated refusals.
See [the commands, guarantee, and recovery contract](docs/withdrawals.md).

The ledger lives at `~/.context-slice/controls/ledger.sqlite3`, **not in the
retrieval cache**. Index rebuilding, source restoration, and receipt resets do
not remove its rules. Index, brief, prepare, read, outline, acknowledgement, and
the metadata-only `control-check` consult the same implementation. Missing or
invalid required controls fail explicitly, rather than falling back to an empty
allow list. Cache and control directories must be disjoint.

This is not semantic forgetting, an ACL service, a claim-admission database, or
interception of other applications. It does not erase existing model contexts,
recognize paraphrases, govern a different OS account, or prevent whole-state
rollback. Source files and historical evidence remain intact. Never bypass a
control refusal with another home, an old standalone client, or a raw-file read.

## Privacy, coverage, and freshness

- Only `.md`, `.mdx`, `.rst`, and `.txt` are indexed. Hidden paths, common build
  directories, symbolic links/reparse points, and a small set of credential
  filenames are excluded. This filename filter is **not** a secret scanner.
- Binary, non-UTF-8, and over-2-MiB files are counted as skipped. An index is a
  scoped discovery aid, not proof that no other evidence exists.
- Source text is never executed. `source_declared_status` is attribution, not a
  verified claim. Raw-evidence markers remain labeled in later excerpts too.
- Matching candidates are SHA-256 checked against current files before use.
  Other documents use filesystem metadata for incremental invalidation. A
  same-metadata edit can affect search completeness; use `index --verify` when
  that matters. Hash checking does not make a filesystem-wide atomic snapshot.
- State defaults to `%LOCALAPPDATA%\context-slice` on Windows and
  `$XDG_CACHE_HOME/context-slice` (or `~/.cache/context-slice`) elsewhere, keyed by
  root. It contains private source text and receipts. Never publish it.
- `--state-dir` is supported, but a cache inside the source corpus or any Git
  worktree is refused. Protect the cache with the same filesystem controls as
  the source. This tool is not an OS sandbox against hostile local processes.
- Unsupported/corrupt indexes and source-access failures are errors, not empty
  success responses. The CLI returns exit code 2 and bounded JSON on stderr.
- Concurrent first use takes an OS file lock before opening competing database
  handles, serializing journal-mode and schema initialization together.
  WAL-mode `SQLITE_BUSY` lock upgrades from other clients have a five-second
  bounded retry; I/O/corruption errors are not retried or concealed.

Only generic source and synthetic examples belong in this repository. Moving
tooling to GitHub does not authorize moving a knowledge corpus or its history.

## Measured example

Original implementation measurement: Windows / Python 3.12, 2026-09-17;
1,500 synthetic Markdown documents, seven
queries, exact planted facts preserved in every returned packet:

| Metric | Full scan + whole matching file | Context Slice |
| --- | ---: | ---: |
| Median warm, in-process retrieval | 565.604 ms | 92.620 ms |
| Median returned UTF-8 bytes | 17,581 | 906 |
| Correct planted facts | 7/7 | 7/7 |

That run returned **94.85% fewer bytes** with **83.62% lower warm retrieval
latency**. Cold indexing took **5.407 seconds**, amortizing in approximately
**12 queries** against this baseline. CLI startup and provider token billing
are not included. These are one-machine synthetic measurements, not universal
savings or evidence of reduced whole-session cost.

## Evaluation contract

The fixed synthetic evaluator compares full-file scan/read with indexed excerpt
retrieval for the same planted facts. It reports cold index time, warm latency,
output bytes, and amortization separately. Acceptance requires every planted fact
to survive, at least 40% lower median warm retrieval latency, and at least 80%
less returned UTF-8 data. Neither metric is a claim about provider token billing.

The implementation experiment is bounded to three candidate runs, sixty seconds
per evaluator run. Correctness and evidence preservation take precedence over
speed. The first candidate was rejected because Windows `stat` and `fstat`
expose different `ctime` semantics. The accepted implementation compares
same-API timestamps and checks file identities separately.

```powershell
python -m unittest discover -s tests -v
python .\benchmarks\measure.py --documents 1500 --repeat 7
python .\benchmarks\quality.py --require-perfect
```

The fixed four-case passage-quality fixture was frozen before the 0.3 search
change. Correct top-result source plus planted fact improved from 2/4 to 4/4,
including path-heavy noise, Chinese text, and filename discovery. This small
synthetic result is not a universal precision or session-speed claim.

GitHub Actions runs the dependency-free tests on Windows, macOS, and Linux. The
fixed evaluator lives in `benchmarks/measure.py`; it generates disposable data
and never reads a real knowledge corpus. Copy the small
[agent adapter fragment](adapters/AGENTS.fragment.md) into an existing workflow
only after reviewing its existing rules; onboarding manages a separate file and
never overwrites an unowned instruction file.

## Publication boundary

This repository contains generic code and synthetic examples only. Git author
and committer identities use GitHub no-reply addresses. Local deployment
receipts, private paths, source corpora, caches, and credentials do not belong in
Git history. Before publication, run:

```powershell
python scripts\audit_publication.py
```

This bounded history/content hygiene check does not replace human source review
or a complete secret-scanning service.
