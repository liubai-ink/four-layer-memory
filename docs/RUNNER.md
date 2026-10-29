<!-- liu bai (留白): the host's half of the contract. Provenance watermark: liu bai. Authors: liubai. -->

# RUNNER.md — what the host must do

The library stores claims and proves their lineage. It cannot decide *what to put
in the prompt*, *when to fold context*, or *whether a citation is real*. Those are
host decisions, and this file is the written contract for them.

If you are an agent runtime, a plugin author, or a person wiring this into an
assistant: implement §1–§5, keep §6's checklist for testing, and treat §7 as the
failure modes this contract exists to prevent.

---

## 0. Why not "inject everything every turn"

Measured on a real store (8 conventions, ~120 preferences):

| payload | size | tokens per turn |
|---|---|---|
| layer A (all conventions) | ~950 chars | ~700 |
| layer B (all preferences) | ~16,600 chars | ~10,000–13,000 |
| one boot read (A + recent B + state + last messages) | ~3,100 chars | ~2,000 |

Injecting A+B into every turn costs about **six** boot reads per turn, and B grows
linearly with the relationship. Worse than the money: a wall of always-on memory
becomes background noise, and a model that cannot tell *its own summary* from *the
user's message* starts answering questions nobody asked. That failure is not
hypothetical — it is why §2 exists.

**Rule:** memory is read on a trigger, not on every turn.

---

## 1. Boot sequence (once per session, before the first reply)

Run this in order. Each step is cheap; skipping one is how a session starts by
confidently remembering the wrong thing.

1. **Health.** `four-layer-memory status` (or read `state.json` directly). If the
   store is not `audit ok`, say so — do not read from a store whose ledger does not
   verify.
2. **A — conventions.** Load *all* of layer A. It is small and it is the law.
3. **B — preferences.** Load the most recent entries **plus** the ones matching the
   current task (by tag or keyword). Never all of B.
4. **State entries.** Entries with `lifecycle=expiring` that have not expired: where
   the user is, what they are doing, what they asked you to wait for.
5. **C — capabilities.** Only if the task needs a tool, route, or skill index.
   On demand, via search.
6. **D — background.** Only through the host's cursor, or when the user explicitly
   asks about the past. Never bulk-load it.
7. **The real transcript.** Read the last 2–3 *actual user messages* from the
   session, not from memory and not from a compaction summary. This is the only
   source that may be quoted as "you said".

If the host has just folded context, step 7 is not optional — it is the difference
between resuming work and hallucinating it.

---

## 2. Citation discipline (the rule that outlives every version)

Every sentence of the form *"you said / you asked / you told me"* must be
checkable. Four buckets, and a claim only ever lives in one of them:

- **USER** — the user's own words, found in the transcript (or a content-hash anchor
  index built from it). Quotable.
- **DOC** — something *you* wrote, addressed to someone else. Attributable to you,
  never to the user.
- **ASSIST** — your inference, plan, or diagnosis. Never quoted, always labelled as
  your judgement.
- **EXT** — an external source. Must carry its URL or citation; never phrased as
  "you said".

Practical checks, in increasing cost:

1. Grep the transcript for the phrase before quoting it.
2. Keep a content-hash index (`sha1[:8]` of the verbatim line) so a quote survives
   re-compaction even when message ids do not.
3. If the search comes back empty: **say "I cannot find that"**. A missing quote is
   cheap; an invented one poisons every decision downstream.

Corollary: a compaction summary is *your* summary (ASSIST). It is evidence of what
happened, never a source of the user's words.

---

## 3. Write path: the model proposes, the host approves

- The model may draft entries. It may not approve them.
- Layer A requires an approval minted by the host's own channel (`approval.mint`),
  bound to the entry's id, text hash, and source event. A stolen approval must not
  transfer: bundles are replayed by re-signing locally, never by copying a signature.
- Sensitive content (medical, identity, financial) goes to quarantine, by keyword,
  **before** any layer rule runs. Do not reword to slip past the gate; the gate is
  the point.
- A NOOP is a decision. `add --noop --reason "duplicate"` records that a candidate
  was read and deliberately not written — otherwise the ledger cannot distinguish
  "considered and rejected" from "never noticed".
- When a newer claim replaces an older one, use `supersedes`. History is appended,
  not rewritten; revocation of the successor restores the predecessor.

---

## 4. Folding / compaction guard

Before the host folds context:

1. **Scan.** Run a pre-fold scan over the not-yet-persisted material. If anything is
   rated BLOCKING, do not fold.
2. **Snapshot.** Back up the store before folding. If the backup fails, do not fold.
3. **Persona first.** Names, promises, relationship details, and tone rules must
   already be in the store (A/B) *before* they are folded — a summary must never be
   the only place they exist.
4. **Keep the user's lines verbatim — by score, not first-come.** Retention is
   `authority × unrecorded × recency`: a persona or convention line the user said
   and that is not in the store yet (P0) must be stored verbatim before the fold;
   an older, already-recorded paraphrase can go. Ordering by arrival time or
   character cap is exactly how the important line becomes the one that is dropped.
5. **A rewrite without an anchor is a lost original.** Every summary/paraphrase
   the fold produces needs a `source_event_id` (or a `supersedes` link) to the
   entry it replaces; `add` warns otherwise and `FLM_REQUIRE_ANCHOR=1` refuses.
   After folding, `store.provenance_coverage()` should stay above 0.90 and
   `store.authority_mix()["derived_share"]` below 0.15 — the store auditing
   itself instead of the host trusting its own summary.
6. **Four-section template.** Every fold produces, at minimum:

```
U: m00042 14:25 [sha8] «the user's verbatim line»
    (one per user message; sha8 = content hash of the quoted text)
Done: what was finished, with paths, numbers, conclusions
State: where the task stands, what remains
Open: one line per unfinished objective
```

7. **Re-read before replying.** After folding, re-read the last real user messages
   (§1.7). Do not answer from the summary.

---

## 5. What stays out of the prompt

- Full layer B every turn.
- Raw layer D (background) — search it instead.
- Quarantine contents (they are unreviewed by definition).
- Any summary of the user as if it were the user.

---

## 6. Minimal implementation checklist

Everything below is CLI/stdlib; no service, no database, no network.

| needed? | piece | why |
|---|---|---|
| **must** | boot read (A + recent B + state + last messages) | §1 |
| **must** | write path with host approval (`add`, `approve`, `quarantine`) | §3 |
| **must** | citation check (grep or hash anchor) | §2 |
| **must** | fold guard (scan, snapshot, verbatim user lines) | §4 |
| recommended | `audit` before trusting a store | catches tampering/lost keys |
| recommended | state entries (`lifecycle=expiring`) | "where am I now" without polling the user |
| optional | BM25 hybrid recall (`search --hybrid`) | CJK word order, morphology |
| optional | `eval_memory.py` | three write-path properties, offline |
| optional | MCP server | tool-call access for agent runtimes |

Test it the way it fails: ask the same factual question in a fresh session, after a
fold, and after a long silence. Consistency across sessions is a property of the
*files plus the reading procedure*, not of a continuous mind.

---

## 7. Failure modes this contract exists to prevent

1. **Summary mistaken for input.** The model answers a question that only exists in
   its own compaction. Check: re-read the last real user messages each turn.
2. **Attribution drift.** The model's diagnosis becomes "the thing you wondered
   about". Check: four buckets, §2.
3. **Silent degradation.** Long context does not make a model dumber in a way you
   can see; it gets vaguer. Check: keep the verbatim lines and the open objectives,
   then diff them after the fold.
4. **Memory as a licence.** Because it "remembers", it persists things it should
   have quarantined. Check: the sensitive gate runs before every layer rule.

None of these are solved by a bigger prompt. They are solved by reading less, on a
trigger, and checking the one thing that matters: *did the user say this?*
