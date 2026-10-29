# Four-Layer Agent Memory · liu bai

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23638826.svg)](https://doi.org/10.5281/zenodo.23638826)

> Long-horizon LLM agents lose themselves in their own context. This repository
> keeps the working memory small and the memory that *matters* on disk, split
> into four layers that different mechanics keep alive.

**Author**: liubai — project **liu bai**.
**Provenance watermark**: `liu bai` (also embedded in exported state files).
**Version**: 0.7.7.

> **Archived releases.** v0.5.0 is deposited on Zenodo: [10.5281/zenodo.23638826](https://doi.org/10.5281/zenodo.23638826) (concept DOI) — version DOI [10.5281/zenodo.23638827](https://zenodo.org/records/23638827). v0.7.7 is the current source release; it is the version the v0.4 review response refers to.

## Download and use in five minutes

```bash
git clone https://github.com/liubai-ink/four-layer-memory && cd four-layer-memory
pip install -e .
four-layer-memory --root ./memory init
four-layer-memory --root ./memory add A "Always answer in Chinese." \
    --source chat:1 --authority user-explicit --evidence quote --source-event evt-1
four-layer-memory --root ./memory approve <entry_id>     # host action; see below
four-layer-memory --root ./memory audit                  # {"ok": true, ...}
```

Then pick how your agent talks to it:

- **MCP** (Claude Desktop, Cursor, any MCP client): run
  `four-layer-memory-mcp --root ./memory` and paste the JSON from
  [docs/INTEGRATING.md](docs/INTEGRATING.md). The model gets eleven tools
  (`memory_load`, `memory_add`, `memory_consolidate`, `memory_status`,
  `memory_audit`, `memory_quarantine`, `memory_review`, `memory_handoff`,
  `memory_recall`, `memory_archive_search`, `memory_archive_verify`) and needs
  no code changes on the host side. None of those tools can approve a write.
- **Every write leaves an archive.** The store keeps an append-only,
  hash-chained log of the raw text behind each decision
  (`archive/events.jsonl`). It is never loaded into a layer, never injected and
  never exported: it exists so a human can audit what was actually written and
  recover the original wording behind a typed entry. `memory_archive_verify`
  recomputes the chain; `memory_archive_search` finds raw text the working set
  no longer holds.
- **Recall is typed.** `memory_recall` searches stored *entries*, not transcript
  chunks: each hit carries its authority, evidence kind, lifecycle and
  revocation state, so a caller can tell "authorised knowledge" from "something
  that was said once". Similarity retrieval cannot make that distinction.
- **Python**: `from four_layer_memory import MemoryStore, Entry` — see
  [docs/INTEGRATING.md](docs/INTEGRATING.md) §2.
- **Claims carry a validity window.** `Entry(valid_from=…, valid_to=…)` records
  *when* a fact is true. The store serves only claims true **now**: a window that
  has closed, or one that has not opened yet, is kept in the ledger and reported
  by `read_layers()["historical"] / ["future"]`, reachable on demand with
  `search(include_historical=True)`. Superseded facts are hidden, not deleted.
- **The write path is measured, not asserted.** `python scripts/eval_memory.py`
  runs a local, offline, dependency-free battery over the three properties no
  public memory benchmark covers: write-gate precision, revoke residual rate and
  ledger tamper detectability. It prints a JSON report and exits non-zero if any
  of them regresses.
- **Claims are merged on the write side.** `Entry(supersedes=<id>)` records that a
  newer claim replaces an older one. The old bullet stays in the layer file and in
  the ledger, so the history stays readable, but `read_layers()` and `search()` stop
  serving it, `read_layers()["superseded"]` names the replacement, and `audit`
  reports every chain. Revoking or expiring the replacement brings the old claim
  back. `add --noop --reason "..."` records the *decision not to write*: "I looked
  and there was nothing new" is a decision too.
- **Recall can rank instead of match.** `search(..., hybrid=True)` (CLI:
  `search --hybrid`) ranks by BM25 over tokens — Latin words plus CJK single
  characters and bigrams — so `架构记忆` finds `记忆架构`. The default stays an exact
  substring match: predictable, and zero dependencies either way.
- **A rewrite must stay traceable.** A `summary` or `paraphrase` with no
  `source_event_id` (and no `supersedes` link) has replaced the words it
  summarizes: the original can no longer be found. `add` warns, and
  `FLM_REQUIRE_ANCHOR=1` refuses the write. `store.provenance_coverage()` reports
  how many rewrites still point back at their source and
  `store.authority_mix()` reports how much of the store is the agent's own
  inference — `audit` fails once that share passes 15%. These are numbers, not
  promises: "a rewrite is lossy" is measured, not asserted.
- **CLI**: for poking at a store by hand; `approve` lives here because it is a
  host decision, not a model decision.

The host still owns two decisions: *what goes into the prompt* and *when to
consolidate*. [docs/RUNNER.md](docs/RUNNER.md) is the written contract for the
first one — the boot sequence, the citation rules, the fold guard, and the measured
reason **not** to inject all of A and B on every turn.
[prompts/LOADING.md](prompts/LOADING.md) is a shorter paste-ready variant for agent
presets.

## Why four layers

Most "agent memory" systems conflate different problems: *what rules we work
under*, *how this person wants things done*, *what the agent can do*, and *what
happened along the way*. Mixing them means every retrieval drags in everything.
We separate them:

| Layer | Name | Question it answers | Where it lives | Typical size |
|---|---|---|---|---|
| **A** | conventions | What must stay true every time? | rules / agent preset | ≤200 chars each |
| **B** | preferences | What execution defaults does the human expect? | preferences file | ≤300 chars each |
| **C** | capabilities | Which tools, assets and procedures are worth remembering? | capability index | ≤400 chars each |
| **D** | background | What ordinary context is worth keeping? | archive | ≤300 chars each |

Two invariants make the design work:

1. **Provenance is mandatory.** Every entry carries `source` and `at`. A memory
   without a source is a rumour, and rumours poison personalisation.
2. **Working memory holds pointers, not contents.** The agent stores *where the
   last consolidation ended* and *how full the context is*; the tail lives in
   files that are read on demand.

The four layers are a *materialisation* of the contracts below, not a claim that
four is the right number. The same fields and rules in one flat file reproduce
the same behaviour; see the paper, §2.

## Trust boundary (v0.7)

The store treats *who authorised a write* as part of the write, and it treats
"approval" as a host action:

- Layer A accepts only explicit user conventions, quoted, carrying an immutable
  source-event id **and** a signed approval record created out-of-band. Everything
  else is quarantined with a reason. No approval material is ever returned to the
  writing agent: MCP `memory_review` can only **reject**, and accepting is done by
  the host through `four-layer-memory approve <entry_id>` (or
  `approval.mint(...)` + `MemoryStore.approve(...)` in Python).
- Inferred text never reaches a loadable layer; text that looks medical,
  identifying or financial is quarantined in every layer.
- Approval records bind one approval to one entry id, the entry bytes
  (`text_sha256`), the source event, the target scope and the format version, and
  are HMAC-signed by the host approval key (`approval.key`, created by `init`).
  Replaying an old approval, or minting one for a different entry, fails
  verification.
- The ledger is written before the layer file: the write record carries a
  transaction id and the expected layer bytes, the file is appended, then a `bind`
  record hashes the bytes actually on disk. On open, an unbound transaction is
  either materialised or, if the bytes already exist, re-bound; a retried write
  repairs a partial attempt.
- Revocation is durable and transitive: `revoke(source_event_id)` records a
  tombstone, walks `derived_from` chains through the ledger, and the loader filters
  from the ledger on every read. Wiping `state.json` does not resurrect anything,
  and a stale handle cannot write a revoked id back into the store.
- The audit rebuilds the expected bytes of every layer from the ledger and compares
  them with the files, so a hand edit cannot be laundered by a later legitimate
  write. Lines that do not follow the bullet grammar are reported as
  `unknown_blocks` and are never served by the loader.
- There is a single checkpoint (no second `handoff.json`); the ledger head is
  signed, and an exported bundle can be verified with the public key. A verifier
  that has no key material does not get `ok: true` for a signed ledger.

`tests/test_reviewer_probes.py` pins the fourteen v0.6 counterexamples, and
`tests/test_review_probes_v07.py` pins the v0.4-review counterexamples
(R01–R13). `scripts/verify_v07_probes.py` re-runs the review checks against an
installed tree and writes a JSON result.

## Admission: authority is not truth

A memory is not admitted because it sounds like a rule. Write-time admission
separates four axes that the label alone used to conflate:

| Axis | Values | Why it exists |
|---|---|---|
| kind | convention / preference / capability / background | what the statement is about |
| authority | user-explicit / user-implied / observed / inferred / model-generated / none | who put it in force |
| evidence | quote / paraphrase / summary / inference | how faithfully it is carried |
| lifecycle | standing / project-scoped / expiring (with optional scope and expiry) | when it stops being true |

Layer **A** admits only `user-explicit` + `quote` + a source-event id + a valid
approval. Everything else lands in `quarantine/pending.md` with a reason and a
ledger record; nothing is dropped silently. Quarantined entries are resolved by a
human: `four-layer-memory quarantine --reject <id>` discards one;
`four-layer-memory approve <id>` promotes one into its claimed layer (the command
mints the approval record and re-runs the same admission check, so an entry
without a verifiable source event still cannot enter A).

Lifecycle is enforced at read time: `project-scoped` and `expiring` entries with a
`scope`/`expires_at` are not loaded once the scope does not match or the date has
passed. Cleanup is lazy; the audit reports how many entries are currently withheld.

## Quick start

```bash
python -m four_layer_memory demo          # end-to-end smoke test in a temp dir
python -m four_layer_memory --root .memory init
python -m four_layer_memory --root .memory add A "Always report the conclusion first." \
    --source chat:m0012 --authority user-explicit --evidence quote --source-event evt-1
python -m four_layer_memory --root .memory quarantine --list   # what was held back, and why
python -m four_layer_memory --root .memory approve <entry_id>  # host-only promotion
python -m four_layer_memory --root .memory revoke evt-7        # everything from one source event
python -m four_layer_memory --root .memory consolidate --anchor m00042 --usage 0.41
python -m four_layer_memory --root .memory audit
```

Python ≥3.10, standard library only (ed25519 needs the optional `cryptography`).

## Triggers

Context pressure is a scheduling problem, not a prompting trick. Given the
fraction of the window in use, the number of turns since the last consolidation
and the number of entries added, `decide()` returns one of:

`none → check → consolidate → fold → emergency`

Defaults: sample the watermark every three turns above 30 %, every turn above
35 %, consolidate above 38 %, let the host fold above 45 % — and folding is
additionally gated by an explicit `allow_fold` and at least one pending entry —
stop and consolidate above 95 %. Consolidation after a fold is rate-limited by a
cooldown of eight entries, not fifty. They are configuration, not physics.

## Signing (v0.4, extended in v0.7)

`ledger.jsonl` is signed, not only chained.

```bash
four-layer-memory --root store keygen                 # hmac-sha256 key in .signing_key (default)
four-layer-memory --root store keygen --alg ed25519   # ed25519, also writes .signing_key.pub
four-layer-memory --root store init                   # creates signing and approval keys on first init
four-layer-memory --root store audit                  # fails on hash or signature mismatch
```

- **hmac-sha256** (standard library): every ledger record carries `sig_alg` and
  `sig` over its canonical body. Rewriting the whole chain no longer produces a
  self-consistent forgery unless the attacker also holds `.signing_key`.
- **ed25519** (optional, requires `cryptography`): the public key is written next
  to the private key, so an exported bundle can be verified by a third party who
  cannot forge one; a public-only verifier can check signatures but cannot sign.
- `ledger_integrity()` reports `signed: hmac-sha256 | ed25519 | unsigned`. A
  ledger whose records are signed but whose check cannot find key material is
  reported as unverifiable, not as verified; an unsigned store keeps working and
  reports `unsigned`.

## Recovery boundary (v0.7)

Every write is a typed transaction: `write` carries the entry and the bytes it
expects, the file is replaced atomically, then `bind` hashes what is on disk. On
`MemoryStore(...)` construction, an unbound transaction is repaired (the entry is
re-materialised, or re-bound when the bytes already exist). This covers a process
that is killed between the two steps, and a retried write after a partial commit.
It does **not** claim protection from physical power loss, multi-writer
interleaving, or compaction; those are out of scope and listed in the paper's
non-claims.

## Hardening (v0.3)

- **One writer at a time.** A store-wide lock (`<root>/.lock`) serialises
  `add`, `revoke`, `review`, and `consolidate`. Batched writes use `add_many` so a
  batch shares one state update.
- **Crash-shaped writes.** Every file write goes through an atomic replace
  (`mkstemp` → `fsync` → `os.replace`), so a killed process leaves either the old
  file or the new one, never half of each.
- **Tamper-evident ledger.** `ledger.jsonl` is a hash chain: each record carries
  `prev` and `hash` over canonical JSON. `ledger_integrity()` recomputes the chain,
  and `audit` fails when it breaks.
- **Human review of the quarantine.** `quarantine --list` shows what was held back;
  `approve ID` promotes a quarantined entry (host channel); `quarantine --reject ID`
  discards it. Reviewing twice raises.
- **Thresholds from data, not taste.** `calibrate --file usage.jsonl` places the
  watermarks at quantiles of observed context usage, keeps
  `consolidate_at <= fold_at - 0.05`, and refuses logs with fewer than 20 samples.

The threat model, with residual risk for each control, is in
[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Repository layout

```
four_layer_memory/   layers, admission, approval, store, triggers, distill, handoff, audit, signing, cli
tests/               pytest suite (stdlib)
scripts/             verify_release.py, verify_v07_probes.py
examples/            candidate memories and handoff records
docs/                ARCHITECTURE, INTEGRATING, THREAT-MODEL, REVIEW-RESPONSE-v04
```

## Compatibility

v0.7 is intentionally source-compatible for the common path (`add`, `add_many`,
`revoke`, `consolidate`, `status`, `quarantine_entries`, `export_bundle`) and
intentionally **incompatible** for two v0.6 affordances: `MemoryStore.review(accept=True)`
now raises (acceptance must come from the host approval channel), and quarantine
entries no longer carry an `approval_token`. `scripts/verify_v07_probes.py` shows
the replacement flow.

## What this is not

It is not a vector database and it does not decide *what* to remember. It is a
small, auditable substrate for the decision: given a context budget, which
memories deserve to be written down, in which layer, with which provenance and
under whose authority. It has no benchmark results yet: the claims are about
mechanism and auditability, not about downstream task performance. It does not
claim a snapshot-based replay, a per-line audit, or protection against a
compromised host process.

## License

MIT. Keep the `liu bai` watermark and NOTICE when you reuse the code.
