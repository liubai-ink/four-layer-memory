<!-- liu bai (留白): Four-Layer Agent Memory. Provenance watermark: liu bai. Authors: liubai. -->

# Changelog

## 0.7.4 - 2026-10-29

### `init --from-bundle`: the portability claim is now real

`docs/PORTABILITY.md` promised that "an exported bundle can be re-imported into
a blank deployment", but `init` only accepted `--session`. Implemented:

- `Store.import_bundle(bundle, approver="host", force=False)` replays the
  bundle's ledger records — layer bullets carry no `source_event_id`, so the
  ledger is the only faithful source. `write` records are re-added as-is;
  `approve` records are re-signed by the **importing** host (an exported
  signature is bound to the exporter's key and deliberately does not travel);
  revocations are replayed by `source_event_id` to rebuild the closure. The
  bundle's `state` is restored, and an `import` ledger event records
  `bundle_sha256` plus the origin session.
- A tampered bundle is refused up front: `verify_ledger_records()` recomputes
  the ledger hash chain instead of trusting the exporter's `ledger_integrity`
  claim. Importing into a non-empty store requires `force=True`.
- CLI: `four-layer-memory init --from-bundle <path> [--approver host] [--force]`;
  the import report is attached to the printed status under `import`.
- `tests/test_portability.py`: 6 cases — round-trip (incl. an A-layer entry and
  a revoke, with `revoked_ids()` equality), tampered watermark, tampered ledger
  row, non-empty target, the CLI path, and the provenance note. Full suite: 100.

### Provenance and version alignment

- Bundle provenance note now reads `liu bai (留白) — joint project by liubai`, matching `LICENSE`
  (it previously said "Liu Zhu & Shen Bai").
- Version aligned to 0.7.4 across `pyproject.toml`, `README.md`,
  `START-HERE.md`, `CODEX.md`, `CITATION.cff` (they disagreed: 0.6.1 / 0.7.2 /
  0.7.3).
- MCP tool count corrected to 11 in `CODEX.md`, `docs/PORTABILITY.md` and
  `START-HERE.md` (the latter listed a non-existent `memory_init`).
- Documented key filename corrected to `approval.key` (the code creates
  `approval.key`; docs said `.approval_key`) in `docs/INTEGRATING.md` and
  `docs/THREAT-MODEL.md`.
- Optional extras added: `ed25519 = ["cryptography>=41"]`,
  `test = ["pytest>=7"]`.
- CI: `.github/workflows/ci.yml` runs the suite on Python 3.10 / 3.11 / 3.12.

### Attribution

- Authorship unified under the joint name `liubai` (the project mark `liu bai`
  stays as the provenance watermark). `LICENSE`, `NOTICE`, `CITATION.cff`,
  `pyproject.toml`, file headers and the bundle provenance note now read
  `liubai`.

## 0.7.3 - 2026-10-28

### Revoked entries no longer fail the audit

`revoke()` writes a tombstone and leaves the original `write`/`approve` records
in the ledger, by design — the ledger is a replay log. The authorization check,
however, re-verified every A-layer `approve` record in the ledger, including
entries that had already been revoked. A store that migrated a legacy entry
(revoke the old one, write a signed replacement) therefore stayed permanently
red: the old record lacked an approval record and would never gain one.

- `audit.py`: before re-verifying an `approve`/`write` record, skip it when its
  `entry_id` (or `source_event_id`) is in the revoked closure; report the count
  under `authorization.revoked_skipped`. Revocation is terminal for auditing:
  the tombstone is what the audit sees, not the superseded record.
- `tests/__init__.py`: added the package marker, so the suite runs with plain
  `python -m pytest -q` (relative imports in four test modules had made
  collection fail). 88 tests pass.

### Compaction: remove revoked blocks from the layer files

`revoke()` tombstones a decision but never touched the layer files, so a store
that migrated entries kept both the dead text and the live replacement in the
same file forever — the file grew, and the audit's structural check compared
counts against a file that still contained revoked blocks.

- `store.compact(apply=False, layers=None)`: the layer file is rebuilt from the
  ledger, dropping every block inside the revoked closure (by `entry_id`,
  `source_event_id`, or `derived_from`). Dry-run by default; `_guard_bytes`
  refuses to write when the on-disk file diverges from what the ledger predicts;
  a timestamped backup is taken before the atomic replace, and the ledger gets a
  `compact` event carrying `dropped_ids`, `before_sha256`, `after_sha256`, so the
  rewrite itself is replayable and auditable.
- `expected_layers()` / `_layer_from_ledger()` skip dropped ids, so a second
  compaction sees the predicted bytes, not the pre-compaction ones.
- `state.layer_counts` is now recomputed from the physical files
  (`_physical_counts()`) in `recover()`/`revoke()`, not from the ledger (a ledger
  count never subtracts revocations).
- `cli`: `compact` subcommand with `--apply`, `--layer`, `--audit-after`.
- `tests/test_compact.py`: 6 tests (dry-run leaves files untouched; apply drops
  only revoked blocks, keeps the audit green, is idempotent, and the servable set
  is correct; no revocations => no-op; a hand-edited file is refused; `--layer`
  limits scope; CLI path). 94 tests pass.

Measured on this machine's real store: conventions.md went from 13 blocks to 6,
`audit` returned `ok=True` with `revoked_skipped=12`.

## 0.7.2 - 2026-10-28

### The archive: raw text behind every decision, never served

A fifth destination that never enters the working set, shaped after a sibling
system's "full card" but kept inside the typed substrate.

- **`archive/events.jsonl`** - append-only, one line per decision (`write`,
  `approve`, `revoke`, `reconstructed`), each carrying `prev_sha256`; the raw
  text is kept verbatim so the original wording behind a typed entry survives.
  The archive is not a layer: never loaded, never injected, never exported.
- **Capture before materialisation.** The decision is archived right after the
  ledger's `write` record and before the layer file is written, so a crash in
  between leaves an archive that already covers the decision; recovery marks
  such events `reconstructed` rather than pretending nothing was lost.
- **Coverage is audited.** `audit` gains an `archive` block: the chain must
  verify and every served write/approve must have a capture
  (`archive_missing_capture` / `archive_chain_broken` / `archive_error`).
- **Two tools, no approval rights.** `memory_archive_search` (FTS5, optional
  `include_revoked`) and `memory_archive_verify` (chain + coverage). Neither can
  write, approve or inject.
- Tests: `tests/test_archive.py` (eight cases); full suite 88 tests.

## 0.7.1 - 2026-10-26

### Fourth review: closable gaps, one contract change (v0.7.1)
Seven findings reproduced against v0.7.0; each is now a named regression test.

- **F01 truncation resurrection** — the store records `state.ledger_records` and
  `state.ledger_head`; a truncated signed suffix is never repaired by raising the
  claim. Reads and writes are refused while truncated, and a repair is recorded
  in `state.recovered`. (test_store_f01_truncation)
- **F02 multi-hop revocation** — `derived_from` now holds typed references (entry
  ids *and* source events); `_closure` resolves through an event-owner map and
  runs to a fixed point, so grandchildren of a revoked event are invalidated.
  (test_store_f02_multihop_revocation)
- **F03 interrupted transactions** — recovery completes a write whose bind record
  is missing and an approval whose bind is missing, rebuilds the resolved set
  from ledger records, and `approve()` is idempotent by transaction identity.
  (test_store_f03_*)
- **F04 caller-supplied approval** — `add(..., approved=True)` is refused with a
  `StoreError`; promotion requires a host-minted, signed approval record whose
  text hash and source event match, and the record is embedded in the ledger so
  the audit can verify it without the host. **Contract change**: tests that wrote
  binding-layer entries through the library now mint an approval record first.
  (test_store_f04_approval_required, test_review_probes_v07)
- **F05 dropped expiry and scope** — CLI and MCP pass `scope`/`expires_at`
  through, record them, filter them at load and count expiries in `status`.
  (test_mcp_f05_scope_expiry)
- **F06 unvalidated exposure** — `read_layers()` reconstructs the servable view
  from committed records instead of reading the layer files, refuses unbound
  edits, and returns unknown blocks as counts on a diagnostics channel rather
  than echoing their text. (test_store_f06_unbound_not_served)
- **F07 audit aggregation** — content and authorization checks emit structured
  findings with codes; the verdict is the conjunction of the four sub-checks.
  (test_audit_f07_secret_blocks)

Also: `audit` now reports `a_without_approval` / `approval_unverified` /
`approval_text_mismatch` / `approval_source_mismatch`; `status()` reports
`ledger_head`, `ledger_truncated` and `expired`.

Test suite: 80 tests. Independent reviewer probes: 29 checks, 0 demonstrated.
## 0.7.0 - 2026-10-26

### Typed recall (v0.7.0)
- New `MemoryStore.search(query, layer=None, include_revoked=False,
  include_expired=False, limit=20)` and MCP tool `memory_recall`. Retrieval
  returns entries with `authority`, `evidence`, `lifecycle`, `scope` and a
  `revoked` flag instead of transcript chunks, so a host can distinguish
  authorised knowledge from an unverified remark. Revoked and expired entries
  are filtered by default; `include_revoked=True` shows what was retracted.
- 6 regression tests in `tests/test_recall.py` (typed fields, exact-substring
  behaviour, revocation filter, layer filter/limit, unknown layer).

Third review round (v0.4 manuscript). Six groups of fixes, all pinned as
regression tests; the counterexample list is R01-R13 in
`tests/test_review_probes_v07.py` and `scripts/verify_v07_probes.py` confirms
19/19 checks against an installed tree (`_v07/result.json`).

### Approval is a host action (F01)
- New `four_layer_memory/approval.py`: an approval record binds one approval to
  one entry id, the entry's `text_sha256`, its source event, the target scope
  (`layer:A`) and the format version, and is HMAC-signed with `.approval_key`
  (created by `init`). `MemoryStore.approve(entry_id, record)` verifies the
  record, re-runs `admission.decide(approved=True)`, and refuses entries whose
  source event was revoked.
- MCP no longer returns any approval material: `memory_add` reports a
  quarantine reason only, and `memory_review` can only reject (`accept=true`
  raises). Acceptance is a CLI/Python host action: `four-layer-memory approve
  <entry_id>`. `quarantine --accept` and `approval_token` are gone.
- `admission.decide` is now the single gate for both `add` and `approve`.

### Revocation is durable and transitive (F02)
- `revoke(source_event_id)` records a tombstone and walks `derived_from`
  chains over the ledger (multi-hop); the result lists every revoked entry id.
- Revocation is recomputed from the ledger on every open and every load:
  losing `state.json`, or a stale handle doing `consolidate`, cannot resurrect
  an entry. The loader filters revoked/expired entries, not raw file text.
- `add`/`add_many`/`approve` quarantine lineage that points at a revoked source
  or entry instead of loading it.

### Crash recovery and idempotency (F03)
- Every write is a typed transaction: `write` carries the entry plus the
  expected layer bytes; the file is replaced; `bind` hashes the bytes on disk.
  On open, an unbound transaction is materialised or re-bound; a retried `add`
  repairs a partial commit, and a foreign file change is refused instead of
  silently absorbed (`_guard_bytes`).
- `Handoff.resume_report()` returns `cursor_valid`, `replayed_from_start`,
  `store_seq` and `recovered`; there is still a single cursor (no snapshot).
- `MemoryStore(..., recover=False)` exists so the audit can report without
  repairing.

### Audit is derived from the ledger (F04)
- `expected_layers()` rebuilds each layer's bytes from write/approve records;
  the audit compares files to that and reports divergence, orphan entries and
  unbound transactions.
- A strict loader grammar: only bullet blocks with metadata are records. Any
  other line is reported as `unknown_blocks` and never served by
  `memory_load`/`read_layers`.

### Signatures and admission details (F05, F06)
- `signing.material()` / `verify_with_material()` distinguish "no key material"
  from "verified": a signed ledger with only public material is checked with the
  public key, and a signature that cannot be checked is not reported as valid.
- Sensitive patterns use word boundaries, so `archive`/`shiver` no longer match
  `hiv`; `authority` and `evidence` gained `none`; `lifecycle` scope/expiry is
  enforced by the loader (with `scope`/`expires_at` fields).
- Version strings unified at 0.7.0 (`__version__`, MCP server version, pyproject).

### Tests and docs
- `pytest`: 75 passed (60 v0.6 tests migrated to the host-approval flow, 15 new
  review regressions). `scripts/verify_v07_probes.py`: 19 checks, 19
  `fix_confirmed`, 0 probe errors.
- README, ARCHITECTURE, THREAT-MODEL, INTEGRATING and LOADING updated to the
  v0.7 trust boundary; `docs/REVIEW-RESPONSE-v04.md` maps F01-F06 and R01-R13
  to code, tests and paper v0.5.

## 0.6.1 - 2026-10-25

- Align `prompts/LOADING.md` with the implemented policy: five-state watermark
  (0.30 / 0.35 / 0.38 / 0.45 / 0.95), fold requires an explicit permission and at
  least one pending entry, cooldown of eight entries, single cursor.
- Loading table now states that inferred text and sensitive categories go to
  quarantine in every layer, and that layer A requires an immutable source event
  plus a human approval record.
- Package a review bundle carrying the anonymous manuscript (v0.4) next to this
  release so readers can compare document and artifact.
- Re-run the reviewer's fourteen probes against this package: 14/14 are now
  blocked (results in `_review/after_v061.json`). The regression suite pins
  each one in `tests/test_reviewer_probes.py`; `scripts/verify_release.py`
  reports 37 files with no problems and `python -m unittest` passes 60 tests.

## 0.6.0 - 2026-10-25

Trust-boundary release.  An external review reproduced fourteen counterexamples
against 0.5.0; all fourteen are now closed, and each one is pinned as a
regression test in `tests/test_reviewer_probes.py`.

### Admission is a runtime decision, not a caller claim
- `admission.decide()` takes the runtime's stamp: layer, authority, evidence,
  `source_event_id` and an explicit `approved` flag.
- Layer A accepts only `user-explicit` + `quote` **with** a source event id and
  an approval record; other writes to A are quarantined.
- Layer B accepts explicit or implied user preferences with quoted or paraphrased
  evidence; inferred text is quarantined instead of silently loaded.
- Text that looks medical, identifying or financial is quarantined in **every**
  layer, including C.
- Quarantine returns a one-time `approval_token`; promotion requires that token,
  and the approving human is recorded.

### Writes, recovery and revocation
- The ledger record is written before the layer file, and a follow-up `bind`
  record carries the sha256 of the bytes that actually landed on disk.
- `add()` is idempotent by entry id; a crash between ledger and layer leaves a
  recoverable state rather than an unlogged entry.
- `review()` preserves `source_event_id` / `derived_from`, so promotion cannot
  detach an entry from the event that authorised it.
- `revoke(source_event)` records the revocation in state, the loader filters
  revoked entries, and entries derived from the revoked event are included.

### One checkpoint, one chain
- `handoff.json` is gone: the cursor lives in `state.json` and `Handoff` is a
  view over it, so a crash cannot leave two disagreeing checkpoints.
- The ledger records its own length in state; a truncated suffix is reported as
  truncation instead of passing as a valid prefix.
- The chain head is signed (HMAC-SHA256 by default, Ed25519 optional) and a
  third party can verify with the public key alone.

### Replayable audit
- `audit` reports four independent checks: structural, content, authorization and
  ledger.  Authorization replays the ledger and compares each layer file against
  its `bind` hash, so text edited outside the store is caught.

### Compatibility note
Two of the reviewer's probes were written before authority became mandatory.  In
the harness that replays them, the two writes that are supposed to land in a
layer are stamped with the authority an interactive runtime would record.  No
product behaviour was relaxed: the probes' own assertions run unchanged.

## 0.5.0 - 2026-10-25

- New: our-layer-memory-mcp, a dependency-free MCP server over stdio. Eight
  tools (memory_load, memory_add, memory_consolidate, memory_status,
  memory_audit, memory_quarantine, memory_review, memory_handoff), so any
  MCP-capable host can use the store without host-side code.
- New: docs/INTEGRATING.md (CLI, Python API, MCP configuration, the host-side
  contract) and prompts/LOADING.md (the loading/writing/consolidation contract).
- New: console scripts our-layer-memory and our-layer-memory-mcp.
- Tests: 	ests/test_mcp.py spawns the server as a real subprocess and checks
  initialize, tool listing, admission, quarantine and audit; suite is 46 tests.

## 0.4.2 - 2026-10-25

- Fix (breaking for new stores): init and keygen crashed with NameError: signing;
  four subcommands (keygen, quarantine, calibrate, 
evoke) were declared in the
  parser but missing from the dispatch table, so they raised KeyError.
- Fix: Entry defaults were uthority=user-explicit / evidence=quote, which let any
  programmatic write into layer A. Defaults are now inferred / summary; layer A must
  be earned by explicit authority or by human review of the quarantine. The CLI exposes
  --authority, --evidence, --lifecycle, --source-event and reports quarantined
  for rejected writes.
- Fix: quarantine_entries() returned already-resolved entries, so --list never shrank.
- Tests: 	ests/test_cli.py covers dispatch, quarantine accept, and explicit-authority
  admission; suite is now 43 tests.

## 0.4.1 - 2026-10-25

- Fix: the ledger hash chain broke when a store was reopened in a new process
  (_last_hash restarted empty, so prev no longer matched the committed head).
  The constructor now reloads the tail hash from ledger.jsonl.
- Tests: two regressions (	ests/test_ledger_reopen.py) covering append-after-reopen
  and review-after-reopen; suite is now 39 tests.

## 0.4.0 - 2026-10-25

Ledger signing.

- new module `signing.py`: `keygen(root, alg)` (`hmac-sha256` by default, or
  `ed25519` when `cryptography` is installed), `sign`, `verify`,
  `public_key_hex`, `algorithm`, `has_key`.
- store: every ledger record is signed when a key exists; `ledger_integrity()`
  verifies signatures after the hash chain and reports the signing algorithm.
- cli: `keygen [--alg hmac-sha256|ed25519] [--force]`; `init` creates a key.
- export: bundles carry `signing` metadata (algorithm and key fingerprint via
  `MemoryStore.signing_info()`).
- tests: `tests/test_signing.py` (5 cases: hmac roundtrip, signed ledger intact,
  recomputed-chain forgery rejected, ed25519 roundtrip when available, unsigned
  stores still work). 37 tests total.
- docs: README "Signing" section; THREAT-MODEL T3 residual risk reduced from
  "rewrite all records" to "forge requires the key file".

## 0.3.0 - 2026-10-25

Hardening pass. No behaviour change to the four layers or to admission rules
beyond what 0.2.0 introduced.

- store: store-wide file lock (Windows msvcrt / POSIX cntl), atomic writes
  via mkstemp + sync + os.replace, dd_many batch write, quarantine
  
eview(accept|reject) with an append-only quarantine/resolved.jsonl, and a
  hash chain in ledger.jsonl with ledger_integrity().
- new module calibrate.py: calibrate(samples) -> quantile thresholds with
  hard bounds and a monotone check; load_samples, write_thresholds.
- audit: a fourth check, ledger, recomputing the chain; ok now requires
  structural, content, authorization, and ledger to pass.
- cli: quarantine --accept/--reject/--list, calibrate --file [--out].
- tests: 	ests/test_hardening.py (9 cases: concurrent writers, tamper
  detection, temp-file check, approve/reject, double review, batch add,
  calibration monotonicity, calibration refusal). 32 tests total.
- docs: docs/THREAT-MODEL.md (ten threats, controls, residual risk, and the
  four things the model does not claim).

## 0.2.0 (2026-10-25)

Responds to an external review of the v0.1 concept paper. The substantive
change is that write-time admission is now tested rather than asserted.

### Added
- our_layer_memory/admission.py: four-axis decision (kind, uthority,
  evidence, lifecycle). Layer A requires user-explicit + quote.
- quarantine/pending.md + ledger.jsonl: rejected writes are recorded with a
  reason instead of being dropped.
- 
evoke <source_event>: revocation propagates to entries derived from the
  event.
- 	ests/test_admission.py: four adversarial admission tests plus revocation
  and audit-shape tests. Suite: 23 tests.

### Changed
- udit reports three separate checks: structural, content, authorization.
- Entry gained entry_id, uthority, evidence, lifecycle,
  source_event_id, derived_from, 
evoked_at.
- export_bundle() includes the quarantine queue and the ledger.

### Known gaps
- No benchmark; the 20-stream x 3-condition paired experiment is specified in
  the paper's Appendix E but not yet run.
- No multi-agent store, no concurrency control beyond atomic file replace.
