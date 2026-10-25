<!-- liu bai (留白) - Four-Layer Agent Memory
     Authors: Liu Zhu (刘竹) and Shen Bai (沈白)
     Provenance watermark: liu bai -->

# Architecture: four layers and three mechanisms

## 1. The problem

An agent that runs for months faces a budget mismatch: the conversation is
unbounded, the context window is not. Summarisation alone degrades
monotonically — every pass loses details that were never written down. The
alternative is to treat memory as a **write-ahead log with typed destinations**:
when a checkpoint fires, the delta is classified into layers and written to
files, and the context keeps only a cursor.

## 2. The four layers

**A — conventions.** Stable rules of engagement. They change rarely, are read
on every turn, and must be phrased as invariants ("always…", "never…"). If a
rule needs a paragraph, it belongs in C, not A.

**B — preferences.** Execution defaults: formats, ordering, tone, tool habits.
They are *not* rules of correctness; they are the person's style. They expire:
a preference recorded six months ago may be wrong today, so entries carry a
date and are revised rather than silently inherited.

**C — capabilities.** Tools, assets, procedures. The agent should be able to
recover "where is the corpus and how do I query it" without re-reading a month
of chat. Capability entries are pointers plus the smallest working example.

**D — background.** The long tail: ordinary facts, anecdotes, moods. It is
stored because continuity matters, but it is retrieved statistically, not
deterministically, and it must never crowd out A or B.

The separation is load-bearing: mixing layers means a retrieval for "how do I
format a table" can pull in a month of health updates.

## 3. Three mechanisms

**Watermark triggers.** The agent knows the context usage *from the host*, not
by guessing. A monotone policy maps (usage, turns since consolidation, entries
since consolidation) to an action. Consolidation happens **before** folding, so
the written record does not depend on a lossy compression step.

**Distillation with mandatory provenance.** Classification is cheap and
rule-first; an LLM can improve it, but the validator rejects any entry without
a source reference or with a secret-like payload. Entries are length-capped per
layer, which forces compression and makes review possible.

**Cursor-based handoff.** A new session or a different client resumes from a
cursor, not from a transcript: read the records after `cursor_ref`, replay only
the delta. If the cursor is lost, replay everything — favour redundancy over
silent gaps.

## 4. Why pointers instead of retrieval

Vector retrieval answers "which chunk is similar to this query". Continuity
asks a different question: "what must this agent still know to be the same
agent for this person". The answer is not necessarily the most similar text; it
is the text that belongs to an invariant (A), a style (B), a capability (C), or
a shared history (D). Pointers make that ordering explicit and auditable.

## 5. Failure modes

- **Self-reinforcement**: the agent writes down its own outputs as if they were
  facts. Mitigation: `source` must point outside the agent's own writing.
- **Stale preferences**: B entries inherit silently. Mitigation: dates and
  revision notes.
- **Trigger thrash**: consolidating too often burns budget. Mitigation:
  cooldown measured in entries, not seconds.
- **Layer bleed**: D entries rewritten into A by a careless classifier.
  Mitigation: strict validation and human review of A.

## 6. Scope

This is a substrate, not a policy. What to remember, and how to act on it, is a
separate question — one we deliberately keep out of this repository.
