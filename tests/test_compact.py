# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
# -*- coding: utf-8 -*-
"""Compaction: drop revoked bullets from layer files, keep the ledger as truth.

Revocation is a tombstone, so a revoked block stays in the file and the loader
skips it.  ``compact`` rewrites the file to header + live bullets and records a
``compact`` ledger event; ``expected_layers`` must then rebuild exactly the
compacted bytes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from four_layer_memory.audit import audit_store
from four_layer_memory.store import MemoryStore, StoreError
from . import helpers


def _root(tmp: str) -> Path:
    return Path(tmp) / "store"


class CompactTest(unittest.TestCase):
    def _two_rules(self, store: MemoryStore):
        keep = helpers.add_a(store, "Always keep provenance on every entry.",
                             source="m1", source_event_id="evt-keep")
        gone = helpers.add_a(store, "Never mention the secret in summaries.",
                             source="m2", source_event_id="evt-gone")
        return keep, gone

    def test_dry_run_reports_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            _keep, gone = self._two_rules(store)
            store.revoke("evt-gone")
            before = store.path_for("A").read_text(encoding="utf-8")
            report = store.compact()
            self.assertFalse(report["applied"])
            layer = report["layers"]["conventions.md"]
            self.assertEqual(1, layer["dropped"])
            self.assertEqual([gone.entry_id], layer["dropped_ids"])
            self.assertEqual(before, store.path_for("A").read_text(encoding="utf-8"))
            self.assertTrue(audit_store(root)["ok"])

    def test_apply_drops_only_revoked_and_keeps_audit_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            keep, gone = self._two_rules(store)
            store.revoke("evt-gone")
            report = store.compact(apply=True)
            self.assertTrue(report["applied"])
            text = store.path_for("A").read_text(encoding="utf-8")
            self.assertIn(keep.entry_id, text)
            self.assertNotIn(gone.entry_id, text)
            self.assertNotIn(keep.entry_id, store.revoked_ids())
            self.assertIn(gone.entry_id, store.revoked_ids())
            # ledger-derived bytes == file bytes after the compact event
            self.assertEqual(store.expected_layers()["A"], text)
            self.assertEqual(1, store.status()["layer_counts"]["conventions"])
            # a second run is a no-op
            self.assertEqual([], store.compact()["dropped"])
            loaded = store.read_layers(["A"])
            self.assertIn("Always keep provenance", loaded["text"])
            self.assertNotIn("Never mention the secret", loaded["text"])
            self.assertTrue(audit_store(root)["ok"])

    def test_no_revocation_means_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            self._two_rules(store)
            before = store.path_for("A").read_text(encoding="utf-8")
            report = store.compact(apply=True)
            self.assertEqual([], report["dropped"])
            self.assertEqual(before, store.path_for("A").read_text(encoding="utf-8"))
            self.assertTrue(audit_store(root)["ok"])

    def test_refuses_to_rewrite_a_diverged_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            self._two_rules(store)
            store.revoke("evt-gone")
            path = store.path_for("A")
            path.write_text(path.read_text(encoding="utf-8") + "- hand edit\n",
                            encoding="utf-8")
            with self.assertRaises(StoreError):
                store.compact(apply=True)

    def test_compaction_of_one_layer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            _keep, gone = self._two_rules(store)
            store.revoke("evt-gone")
            report = store.compact(apply=True, layers=["B"])
            self.assertEqual([], report["dropped"])
            self.assertIn(gone.entry_id, store.path_for("A").read_text(encoding="utf-8"))
            self.assertTrue(audit_store(root)["ok"])

    def test_cli_compact_dry_run_then_apply(self) -> None:
        from four_layer_memory import cli
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            store = MemoryStore(root)
            _keep, _gone = self._two_rules(store)
            store.revoke("evt-gone")
            self.assertEqual(0, cli.main(["--root", str(root), "compact"]))
            self.assertEqual(0, cli.main(["--root", str(root), "compact", "--apply",
                                          "--audit-after"]))
            self.assertTrue(audit_store(root)["ok"])


if __name__ == "__main__":
    unittest.main()
