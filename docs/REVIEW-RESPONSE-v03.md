<!-- liu bai (留白) - a project by Liu Yinuo
     Four-Layer Agent Memory: conventions, preferences, capabilities, background.
     Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking. -->
# Response to the v0.3 review (14 counterexamples)

We accept the finding that motivated this round: the v0.3 text described a
contract that v0.5.0 did not enforce. Each counterexample below was reproduced
against the published artifact before we changed anything, and each is now a
regression test (`tests/test_reviewer_probes.py`). Against the v0.6.0 tree the
reviewer's script reports `reproduced = 0 / 14`.

## Status per probe

| Probe | Failure in 0.5.0 | v0.6.0 | Fix |
|---|---|---|---|
| P01 claimed authority | caller could stamp `user-explicit` and land in layer A | closed | admission takes the runtime's stamp; A needs a source event id and an approval record |
| P02 agent self-approval | MCP `memory_review` approved by default | closed | review requires the one-time token issued at quarantine; approve is recorded |
| P03 ungated default load | inferred text in layer B was loaded every turn | closed | B accepts explicit/implied user preferences only; inferred text is quarantined |
| P04 revoked still loaded | `revoke()` only logged | closed | revocation is state; the loader filters revoked entries |
| P05 source lost on promotion | `review()` rebuilt the entry without `source_event_id` | closed | promotion preserves `source_event_id` / `derived_from` |
| P06 unlogged write passes audit | ledger written after the layer file | closed | ledger first, then layer, then a `bind` record with the on-disk hash |
| P07 mixed checkpoint | `handoff.json` and `state.json` could disagree | closed | one cursor in state; `Handoff` is a view |
| P08 duplicate replay | the same entry id could be written twice | closed | idempotent add by entry id |
| P09 stale handles | two open handles forked the ledger chain | closed | append re-reads the tail under the file lock |
| P10 truncated suffix | deleting the last signed record looked valid | closed | the ledger's length is recorded in state; truncation is reported |
| P11 layer not bound | editing layer text left the ledger intact | closed | `bind` records hash the bytes actually written; audit compares |
| P12 fold without consolidation | >45% asked for fold with nothing fresh | closed | fold requires `allow_fold` and no pending entries |
| P13 sensitive text in C | the sensitive-text rule did not cover C | closed | medical/identifying/financial text is quarantined in every layer |
| P14 public-only verification | verification needed the private key | closed | verify falls back to the public key (HMAC digest, or Ed25519 `.signing_key.pub`) |

## Two harness adjustments, stated openly

The reviewer's probes were written against the 0.5.0 contract, and two of them
presuppose a write that **lands in** a layer:

1. `store.review(entry_id, True)` — promotion now requires the token shown at
   quarantine time, so the harness reads that token from `quarantine_entries()`.
2. P10 and P11 create layer-B writes with no authority — under the v0.6 contract
   a runtime must record who authorised the write, so those two writes are
   stamped `authority=user-explicit, evidence=quote`.

No product assertion was relaxed; the probes' own checks run unchanged. The
harness is `repro-v03/_reproduce_compat.py` and the result file is
`repro-v03/after5.json`.

## The strongest counter-argument in the review

The review argues that a single store can carry every type semantic (kind,
binding, source event, authorisation event, evidence, scope, expiry, derived
edges, tombstones) with the same thresholds and the same retrieval budget, so
the four layers are a deployment configuration rather than a model.

We accept this as the correct framing, and will state it in the paper instead of
defending the layer count. What we will claim is narrower and testable:

- **admission before write**: a memory is only allowed to bind behaviour if the
  runtime recorded who authorised it, what kind of evidence it is, and which
  event it came from;
- **revocation cascade**: withdrawing the source event withdraws everything
  derived from it, including later edits;
- **replayable audit**: the ledger plus `bind` hashes let a third party check
  that what is on disk is what was authorised.

Those three properties can be tested against a single-store baseline; the
paper's experiment section now compares them rather than comparing layer counts.

## What changes in the paper (next revision)

1. Abstract and introduction: drop "none of them answers"; state the admission
   property as the contribution and note the single-store equivalence.
2. §3: present the four axes as a *contract* (authority / evidence / lifecycle /
   kind) and say explicitly that layers are one possible materialisation.
3. §4.3: describe the append-only source log, the ledger-before-layer order, the
   `bind` record, and crash recovery.
4. §5: report the v0.6.0 controls, the fourteen regression probes, and the audit
   checks that are replayed rather than asserted.
5. §7: add "what we do not claim" — no benchmark superiority, no formal
   freshness guarantee, no multi-writer protocol beyond a single lock.
6. Appendix A: replace the paired-stream design with a two-condition comparison
   of *admission property on/off* on the same single store, plus a revocation
   exposure rate.

## Deltas in the artifact

- 60 unit tests (was 46), including the 14 probes.
- `verify_release.py`: 36 files, no problems.
- Release bundle: `four-layer-memory-v0.6.0.zip` (67,384 B, sha256
  `ea6729ea343b514c9189052bef720846b7b295a798ae159c3053f19edd290f70`).

