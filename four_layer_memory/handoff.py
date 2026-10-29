# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Cross-session handoff.

Long-horizon work usually outlives a single chat session.  Instead of copying
the transcript, we keep a *cursor*: which message the last consolidation
reached, and what has changed since.  A new session can then resume by reading
the delta, not the whole history.

There is exactly one cursor.  It lives in ``state.json`` with the rest of the
store state; ``Handoff`` is a view over it.  Earlier versions kept a second
``handoff.json``; a crash between the two writes exposed a mixed checkpoint,
which is why that file no longer exists.

v0.7 makes the handoff *replayable* instead of best-effort (review item R09):

* the state carries ``ledger_records`` (the store sequence number) and
  ``recovered`` (transactions repaired on open), so a new process can tell
  whether it is resuming from the same store generation;
* ``resume_report`` returns a dict with ``cursor_valid``, ``store_seq`` and
  ``recovered`` alongside the records, so a caller can decide to replay instead
  of silently trusting a stale cursor;
* a missing cursor still means "replay everything" - losing memory is worse
  than repeating it - but the caller is told that it happened.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .watermark import WATERMARK


@dataclass
class HandoffState:
    cursor_ref: str = ""
    cursor_at: str = ""
    consolidated_at: str = ""
    note: str = ""
    ledger_records: int = 0
    recovered: list[dict] = field(default_factory=list)
    watermark: str = WATERMARK

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"


class Handoff:
    def __init__(self, path: str | Path):
        from .store import MemoryStore

        self.path = Path(path)
        self._store = MemoryStore(self.path.parent)
        self.last_resume: dict = {}
        self._sync()

    def _sync(self) -> None:
        state = self._store.state
        self.state = HandoffState(
            cursor_ref=state.last_anchor_ref,
            cursor_at=state.consolidated_at,
            consolidated_at=state.consolidated_at,
            note="",
            ledger_records=int(getattr(state, "ledger_records", 0) or 0),
            recovered=list(getattr(state, "recovered", []) or []),
        )

    def mark_consolidated(self, ref: str) -> None:
        self._store.consolidate(ref, self._store.state.usage_at_consolidation)
        self._sync()

    def resume(self, records: list[dict]) -> list[dict]:
        """Return records after the cursor (records must carry a 'ref' field)."""
        return self.resume_report(records)["records"]

    def resume_report(self, records: list[dict]) -> dict:
        """Return the resumable delta plus enough metadata to audit the resume."""
        self._sync()
        if not self.state.cursor_ref:
            self.last_resume = {
                "records": list(records), "cursor_valid": False,
                "replayed_from_start": True, "store_seq": self.state.ledger_records,
                "recovered": list(self.state.recovered),
                "reason": "no cursor recorded; replaying the whole log",
            }
            return self.last_resume
        for i, rec in enumerate(records):
            if rec.get("ref") == self.state.cursor_ref:
                self.last_resume = {
                    "records": records[i + 1:], "cursor_valid": True,
                    "replayed_from_start": False, "store_seq": self.state.ledger_records,
                    "recovered": list(self.state.recovered), "reason": "cursor found",
                }
                return self.last_resume
        self.last_resume = {
            "records": list(records), "cursor_valid": False,
            "replayed_from_start": True, "store_seq": self.state.ledger_records,
            "recovered": list(self.state.recovered),
            "reason": "cursor lost; safer to replay than to drop memory",
        }
        return self.last_resume

    def _save(self) -> None:  # kept for callers that used to force a write
        self._store._save_state()
