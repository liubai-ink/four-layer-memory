<!--
  liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
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

| Signal | Where | Authority |
|---|---|---|
| The user states a standing rule ("always / never / from now on") and you can quote them | A | `user-explicit` + `quote` |
| You infer a habit from repeated behaviour | B | `user-implied` + `paraphrase` |
| A stable tool, path or procedure becomes worth reusing | C | `observed` + `summary` |
| An ordinary fact that may matter later | D | `inferred` + `summary` |

Layer A is the only layer that is *earned*: without explicit authority and a
quotable source, the write is quarantined instead of stored. A human resolves
quarantine by accepting or rejecting the entry; accepted entries carry an
approval tag in the ledger.

Write the smallest text that still makes sense a month later, keep the source
reference (message id, file, URL), and never copy the conversation in.

## When to consolidate

Consolidation is the act of folding new material into the layers and recording a
checkpoint. Trigger it by *measured* context usage, not by feel:

- `usage_ratio >= 0.40` → plan a consolidation at the next natural break;
- `usage_ratio >= 0.45` → consolidate before the host starts folding the context;
- `usage_ratio >= 0.95` → emergency: consolidate now, and tell the user.

After consolidating, write a **handoff** state: the reference of the last message
you folded. A new session resumes from that cursor, not from a transcript.

## What not to do

- Do not treat provenance as truth: `source` records where a claim came from, not
  whether it is correct, and not who authorized it.
- Do not let the model promote its own output into layer A.
- Do not store secrets in any layer; the audit scans for key-shaped strings.
- Do not let the store grow forever: D entries age out, old B entries move to an
  archive, A entries never expire.
