<!--
  liu bai (留白) - a project by Liu Yinuo
  Four-Layer Agent Memory: conventions, preferences, capabilities, background.
  Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
-->
# Loading contract

This file is the part that turns the store into *memory*. It tells the host (and
the model) what to load, when to write, and when to let go. It is deliberately
short: if a rule cannot be applied in one turn, it does not belong here.

## Every turn: A and B

Put layers **A (conventions)** and **B (preferences)** into the system prompt,
verbatim, before the conversation. They are small by construction (200 and 300
characters per entry, respectively) and they are what keeps the agent the same
agent across sessions.

Do **not** paste C and D into every prompt. They are large and usually
irrelevant:

- **C (capabilities)** — load one file or one entry when the task actually needs
  it (a tool, a corpus path, a procedure).
- **D (background)** — load on demand, or not at all: when a new session starts,
  read the *handoff cursor* and the delta after it (see below) instead of the
  whole archive.

## When to write

| Signal | Where | Authority | Evidence |
|---|---|---|---|
| The user states a standing rule ("always / never / from now on") and you can quote them | A | `user-explicit` | `quote` |
| The user states a preference, or you infer one from repeated behaviour | B | `user-explicit` / `user-implied` | `quote` / `paraphrase` |
| A stable tool, path or procedure becomes worth reusing | C | `observed` | `summary` |
| An ordinary fact that may matter later | D | `observed` | `summary` |
| Anything inferred by the model, anything about health, identity, money, or anything whose authority you cannot state | quarantine | — | — |

Layer A is the only layer that is *earned*: the write must carry an immutable
source-event id and a **host approval record**. The model never mints approvals:
`memory_review` can only reject, and acceptance is a host action
(`four-layer-memory approve <entry_id>`). Everything else is quarantined with a
reason; a human resolves it by rejecting the entry or by approving it on the host
side, and accepted entries carry the approval in the ledger.

Write the smallest text that still makes sense a month later, keep the source
reference (message id, file, URL), and never copy the conversation in.

## When to consolidate

Consolidation is the act of folding new material into the layers and recording a
checkpoint. Trigger it by *measured* context usage, not by feel. The policy is a
five-state machine, and the numbers below are the implemented defaults (a host may
calibrate them, but it must then say so):

| State | Threshold | What the host does |
|---|---|---|
| `normal` | < 0.30 | nothing |
| `watch` | ≥ 0.30 | sample every third turn |
| `act` | ≥ 0.35 | check every turn, plan a break |
| `forced` | ≥ 0.38 | consolidate at the next break; respect the cooldown |
| `collapse` | ≥ 0.45 | fold is permitted only with explicit host permission, only after a consolidation, and only if at least one entry is pending |

A cooldown (eight entries, default) prevents re-consolidating the same material:
after a consolidation, the state returns to `watch` only when the cooldown has
elapsed. At `usage_ratio >= 0.95` the host stops accepting new work and finishes
with an explicit short answer.

After consolidating, write the **handoff** state: the reference of the last message
you folded, in the source-event log's coordinate system. There is exactly one
cursor; a new session resumes from it, not from a transcript.

## What not to do

- Do not treat provenance as truth: `source` records where a claim came from, not
  whether it is correct, and not who authorized it.
- Do not let the model promote its own output into layer A, and do not approve on
  the model's behalf: `memory_review` rejects only.
- Do not store secrets in any layer; the audit scans for key-shaped strings.
- Do not let the store grow forever: D entries age out, old B entries move to an
  archive, A entries never expire (project-scoped and expiring entries are
  withheld by the loader once their scope or date is over).
