# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
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
            first.add(Entry("B", "first entry", source="test:1"))

            second = MemoryStore(root)
            second.add(Entry("B", "second entry", source="test:2"))

            integrity = second.ledger_integrity()
            self.assertTrue(integrity["ok"], integrity)
            self.assertEqual(2, integrity["records"])

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
            pending = [
                line.split()[1]
                for line in (root / "quarantine" / "pending.md").read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")
            ]
            reopened.review(pending[0], accept=True)

            integrity = reopened.ledger_integrity()
            self.assertTrue(integrity["ok"], integrity)
            self.assertEqual(2, integrity["records"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
