# Four-Layer Agent Memory · liu bai (留白)

> Long-horizon LLM agents lose themselves in their own context. This repository
> keeps the working memory small and the memory that *matters* on disk, split
> into four layers that different mechanics keep alive.

**Authors**: Liu Zhu (刘竹) and Shen Bai (沈白) — together **liu bai** (留白).
**Provenance watermark**: `liu bai` (also embedded in exported state files).

## Download and use in five minutes

`ash
git clone https://github.com/liubai-ink/four-layer-memory && cd four-layer-memory
pip install -e .
four-layer-memory --root ./memory init
four-layer-memory --root ./memory add A "Always answer in Chinese." --source chat:1 --authority user-explicit --evidence quote
four-layer-memory --root ./memory audit        # {"ok": true, ...}
`

Then pick how your agent talks to it:

- **MCP** (Claude Desktop, Cursor, any MCP client): run our-layer-memory-mcp
  --root ./memory and paste the JSON from [docs/INTEGRATING.md](docs/INTEGRATING.md).
  The model gets eight tools (memory_load, memory_add, memory_consolidate,
  memory_status, memory_audit, memory_quarantine, memory_review,
  memory_handoff) and needs no code changes on the host side.
- **Python**: rom four_layer_memory import MemoryStore, Entry — see
  [docs/INTEGRATING.md](docs/INTEGRATING.md) §2.
- **CLI**: for poking at a store by hand.

The host still owns two decisions: *what goes into the prompt* (load A and B
every turn; C on demand; D through the cursor) and *when to consolidate*.
[prompts/LOADING.md](prompts/LOADING.md) is the written contract to paste into
your agent's instructions.

## Why four layers



Most "agent memory" systems conflate three different problems: *what rules we
work under*, *how this person wants things done*, *what the agent can do*, and
*what happened along the way*. Mixing them means every retrieval drags in
everything. We separate them:

| Layer | Name | Question it answers | Where it lives | Typical size |
|---|---|---|---|---|
| **A** | conventions | What must stay true every time? | rules / agent preset | ≤200 chars each |
| **B** | preferences | What execution defaults does the human expect? | preferences file | ≤300 chars each |
| **C** | capabilities | Which tools, assets and procedures are worth remembering? | capability index | ≤400 chars each |
| **D** | background | What ordinary context is worth keeping? | archive | ≤300 chars each |

Two invariants make the design work (both refined in v0.2):

1. **Provenance is mandatory.** Every entry carries `source` and `at`. A memory
   without a source is a rumour, and rumours poison personalisation.
2. **Working memory holds pointers, not contents.** The agent stores *where the
   last consolidation ended* and *how full the context is*; the tail lives in
   files that are read on demand.

## Admission: authority is not truth

A memory is not admitted because it sounds like a rule. Write-time admission
separates four axes that the label alone used to conflate:

| Axis | Values | Why it exists |
|---|---|---|
| kind | convention / preference / capability / background | what the statement is about |
| uthority | user-explicit / user-implied / observed / inferred / model-generated | who put it in force |
| evidence | quote / paraphrase / summary / inference | how faithfully it is carried |
| lifecycle | standing / project-scoped / expiring | when it stops being true |

Layer **A** admits only user-explicit + quote. Everything else lands in
quarantine/pending.md with a reason and a ledger record; nothing is dropped
silently. Revocation is by source event: 
revoke <event> marks entries that
came from that event, including entries derived from it.

## Quick start

```bash
python -m four_layer_memory demo          # end-to-end smoke test in a temp dir
python -m four_layer_memory --root .memory init
python -m four_layer_memory --root .memory add A "Always report the conclusion first." --source chat:m0012 --authority user-explicit --evidence quote
python -m four_layer_memory --root .memory quarantine    # what was held back, and why
python -m four_layer_memory --root .memory revoke evt-7  # revoke everything from one source event
python -m four_layer_memory --root .memory consolidate --anchor m00042 --usage 0.41
python -m four_layer_memory --root .memory audit
```

Python ≥3.10, standard library only.

## Triggers

Context pressure is a scheduling problem, not a prompting trick. Given the
fraction of the window in use, the number of turns since the last consolidation
and the number of entries added, `decide()` returns one of:

`none → check → consolidate → fold → emergency`

Defaults: sample the watermark every three turns above 30 %, every turn above
35 %, consolidate above 38 %, let the host fold above 45 %, stop and consolidate
above 95 %. They are configuration, not physics.

## Signing (v0.4)

`ledger.jsonl` is now signed, not only chained.

```bash
flm keygen                       # hmac-sha256 key in .signing_key (default)
flm keygen --alg ed25519         # ed25519, also writes .signing_key.pub
flm init --root store            # creates a key on first init
flm audit --root store           # fails on hash or signature mismatch
```

- **hmac-sha256** (standard library): every ledger record carries `sig_alg` and
  `sig` over its canonical body. Rewriting the whole chain no longer produces a
  self-consistent forgery unless the attacker also holds `.signing_key`.
- **ed25519** (optional, requires `cryptography`): the public key is written next
  to the private key, so an exported bundle can be verified by a third party who
  cannot forge one.
- `ledger_integrity()` reports `signed: hmac-sha256 | ed25519 | unsigned`.
  Stores created before a key exists keep working and report `unsigned`; running
  `keygen` starts signing new records, and records written before the key are
  then reported as a signature mismatch until the store is re-seeded.

## Hardening (v0.3)

- **One writer at a time.** A store-wide lock (<root>/.lock) serialises
  dd, 
evoke, 
eview, and consolidate. Batched writes use
  `add_many` so a batch shares one state update.
- **Crash-shaped writes.** Every file write goes through an atomic replace
  (mkstemp -> sync -> os.replace), so a killed process leaves either the
  old file or the new one, never half of each.
- **Tamper-evident ledger.** `ledger.jsonl` is a hash chain: each record
  carries `prev` and `hash` over canonical JSON. `ledger_integrity()`
  recomputes the chain, and `audit` fails when it breaks.
- **Human review of the quarantine.** `quarantine --list` shows what was held
  back; `quarantine --accept ID` promotes an entry into its claimed layer as
  `user-explicit` with an `approve` record; `quarantine --reject ID`
  discards it. Reviewing twice raises.
- **Thresholds from data, not taste.** `calibrate --file usage.jsonl` places
  the watermarks at quantiles of observed context usage, keeps
  `consolidate_at <= fold_at - 0.05`, and refuses logs with fewer than 20
  samples.

The threat model, with residual risk for each control, is in
[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Repository layout

```
four_layer_memory/   layers, admission, store, triggers, distill, handoff, audit, cli
tests/               unittest suite (stdlib)
scripts/             verify_release.py (privacy + provenance pass)
examples/            candidate memories and handoff records
docs/ARCHITECTURE.md the conceptual model, layer by layer
```

## What this is not

It is not a vector database and it does not decide *what* to remember. It is a
small, auditable substrate for the decision: given a context budget, which
memories deserve to be written down, in which layer, with which provenance and
under whose authority. It has no benchmark results yet: the current claims are
about mechanism and auditability, not about downstream task performance.

## License

MIT. Keep the `liu bai` watermark and NOTICE when you reuse the code.
