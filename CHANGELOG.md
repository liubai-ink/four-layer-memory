<!-- liu bai (留白): Four-Layer Agent Memory. Provenance watermark: liu bai. Authors: Liu Zhu (刘竹) and Shen Bai (沈白). -->

# Changelog

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
