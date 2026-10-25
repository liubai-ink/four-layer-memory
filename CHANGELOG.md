<!-- liu bai (留白): Four-Layer Agent Memory. Provenance watermark: liu bai. Authors: Liu Yinuo. -->

# Changelog

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
