# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
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
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .watermark import WATERMARK


@dataclass
class HandoffState:
    cursor_ref: str = ""
    cursor_at: str = ""
    consolidated_at: str = ""
    note: str = ""
    watermark: str = WATERMARK

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"


class Handoff:
    def __init__(self, path: str | Path):
        from .store import MemoryStore

        self.path = Path(path)
        self._store = MemoryStore(self.path.parent)
        self._sync()

    def _sync(self) -> None:
        state = self._store.state
        self.state = HandoffState(
            cursor_ref=state.last_anchor_ref,
            cursor_at=state.consolidated_at,
            consolidated_at=state.consolidated_at,
            note="",
        )

    def mark_consolidated(self, ref: str) -> None:
        self._store.consolidate(ref, self._store.state.usage_at_consolidation)
        self._sync()

    def resume(self, records: list[dict]) -> list[dict]:
        """Return records after the cursor (records must carry a 'ref' field)."""
        self._sync()
        if not self.state.cursor_ref:
            return records
        for i, rec in enumerate(records):
            if rec.get("ref") == self.state.cursor_ref:
                return records[i + 1:]
        return records  # cursor lost: safer to replay than to drop

    def _save(self) -> None:  # kept for callers that used to force a write
        self._store._save_state()
