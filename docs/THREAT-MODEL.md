<!--
liu bai (留白) - a project by Liu Yinuo
Four-Layer Agent Memory: conventions, preferences, capabilities, background.
Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
-->

# Threat model

Scope: a single-host, single-writer deployment of `four_layer_memory` where an
agent process writes and reads a local store, and untrusted text arrives from
documents, tools, and the network. Multi-agent concurrent writers and
distributed deployment are out of scope; the residual risk they create is
listed rather than solved.

Each row names the asset at risk, the control that exists in code, the residual
risk we are not claiming to remove, and the test that exercises the control.

| id | threat | asset | control (code) | residual risk | test |
| -- | ------ | ----- | -------------- | ------------- | ---- |
| T1 | two writers interleave and one update is lost | memory files, state | store-wide lock: `MemoryStore._Lock` on `<root>/.lock` (`msvcrt.locking` / `fcntl.flock`, 5 s timeout); `add_many` takes one lock for the batch | locks are per host; a network share may not honour them | `test_concurrent_writers_do_not_lose_entries` |
| T2 | crash mid-write leaves a truncated layer or state file | memory files, `state.json` | every write goes through `_atomic_write` (`mkstemp` → `fsync` → `os.replace`) | directory entry is not fsynced on Windows | `test_no_temp_files_left_behind` |
| T3 | an attacker (or a buggy tool) edits `ledger.jsonl` to hide a write | audit trail | append-only hash chain plus per-record signature (`signing.py`: hmac-sha256 default, ed25519 optional); `ledger_integrity()` recomputes the chain and verifies signatures; `audit` fails when either breaks | forging the whole chain now requires the signing key file; a compromised host that can read `.signing_key` is outside the boundary | `test_ledger_tampering_is_detected`, `test_recomputed_chain_without_signature_is_rejected` |
| T4 | untrusted document text is promoted into layer A so it starts binding the agent | admission, layer A | admission rules (`Authority`, `Evidence`, `Lifecycle`): A accepts only `user-explicit` + `quote`; everything else waits in `quarantine/pending.md` until a human `review()` | a paraphrase presented as a quote still needs human reading; the rule narrows the channel, it does not read intent | `test_malicious_document_seeking_a_is_quarantined`, `test_quarantine_accept_promotes_with_human_authority` |
| T5 | credentials or absolute paths end up in a memory entry or an export | secrets, machine layout | `audit` content check (key-shaped strings, absolute paths); `MemoryStore.add` rejects secret-shaped text | unusual encodings and split secrets are not detected | `test_audit_detects_secret`, `test_add_rejects_secret` |
| T6 | a revocation leaves copies behind | derived entries | `revoke(source_event_id)` strips entries whose `source_event_id` matches and records derived entries that contain it | hand-copied text with no provenance link is invisible to revocation | `test_revocation_covers_derived_entries` |
| T7 | watermark thresholds misfire (too early: waste; too late: loss) | context budget | `calibrate()` from observed usage, quantile placement, hard bound `consolidate_at <= fold_at - 0.05`, monotone check | calibration reflects the log it was given; distribution drift needs re-running | `test_calibration_is_monotone_and_bounded` |
| T8 | a reviewer approves a quarantined entry twice, or approves after rejection | quarantine record | `review()` raises `StoreError` when the entry is already resolved; `quarantine/resolved.jsonl` is append-only | a lost `resolved.jsonl` returns entries to pending only if the block is still in `pending.md` | `test_double_review_is_rejected` |
| T9 | disk exhaustion from unbounded growth | host disk | per-entry length limit (`MAX_TEXT`); quarantine and ledger are append-only, so growth is monotone | no compaction or retention policy yet | `test_add_rejects_long_text` |
| T10 | two agents write the same store concurrently | memory files | documented as unsupported: single-writer assumption; the lock prevents corruption but not lost intent | last writer wins for semantic conflicts | — (documented gap) |

## What the model deliberately does not claim

- It does not authenticate the *user*. `authority=user-explicit` means "this
  text was quoted from a message the host attributed to the user", not "the
  user is who the host thinks".
- It does not judge *truth*. Provenance is audit metadata; a true statement and
  a false one can carry the same provenance.
- It does not defend against a compromised host process. Any process that can
  read `.lock` and rewrite files is outside the boundary.
- It does not make memory portable across hosts; the store is local-first by
  design.

## Open items

1. ~~Sign the ledger head~~ done in 0.4.0: records are signed (hmac-sha256 by
   default, ed25519 optional); publishing `.signing_key.pub` with a release lets a
   third party verify an exported bundle.
2. Add a retention policy and a compaction pass that preserves the chain (T9).
3. Run the adversarial suite continuously (AgentPoison-style injections) rather
   than only in unit tests.
4. Specify a single-writer lease for the multi-agent case (T10) or declare
   multi-agent out of scope in the paper as well.
