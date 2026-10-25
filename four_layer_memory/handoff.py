# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Cross-session handoff.

Long-horizon work usually outlives a single chat session.  Instead of copying
the transcript, we keep a *cursor*: which message the last consolidation
reached, and what has changed since.  A new session can then resume by reading
the delta, not the whole history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .store import now_iso
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
        self.path = Path(path)
        if self.path.exists():
            self.state = HandoffState(**json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self.state = HandoffState()

    def mark_consolidated(self, ref: str) -> None:
        self.state.cursor_ref = ref
        self.state.cursor_at = now_iso()
        self.state.consolidated_at = now_iso()
        self._save()

    def resume(self, records: list[dict]) -> list[dict]:
        """Return records after the cursor (records must carry a 'ref' field)."""
        if not self.state.cursor_ref:
            return records
        for i, rec in enumerate(records):
            if rec.get("ref") == self.state.cursor_ref:
                return records[i + 1:]
        return records  # cursor lost: safer to replay than to drop

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.state.to_json(), encoding="utf-8")
