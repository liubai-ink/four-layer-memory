# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Regression: the ledger hash chain must survive a process restart."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from four_layer_memory.store import Entry, MemoryStore


class LedgerReopenTest(unittest.TestCase):
    def test_append_after_reopen_keeps_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            first = MemoryStore(root)
            first.add(Entry("B", "first entry", source="test:1",
                        authority="user-explicit", evidence="quote"))

            second = MemoryStore(root)
            second.add(Entry("B", "second entry", source="test:2",
                         authority="user-explicit", evidence="quote"))

            integrity = second.ledger_integrity()
            self.assertTrue(integrity["ok"], integrity)
            ledger_lines = (root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_lines), integrity["records"])

    def test_review_after_reopen_keeps_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            store = MemoryStore(root)
            store.add(
                Entry(
                    "A",
                    "always answer before thinking",
                    source="test:3",
                    authority="user-explicit",
                    evidence="paraphrase",
                )
            )
            self.assertEqual(1, store.quarantined())

            reopened = MemoryStore(root)
            pending_entries = reopened.quarantine_entries()
            reopened.review(
                pending_entries[0]["entry_id"],
                accept=True,
                token=str(pending_entries[0]["approval_token"]),
            )

            integrity = reopened.ledger_integrity()
            self.assertTrue(integrity["ok"], integrity)
            # one quarantine record + the approval (and its write) must all be chained
            self.assertEqual(3, integrity["records"])
            self.assertEqual(
                len((root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()),
                integrity["records"],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
