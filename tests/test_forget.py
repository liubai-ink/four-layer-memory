# liu bai (留白): per-entry forget tests. Provenance watermark: liu bai.
"""P2-14: forget drops one entry from the layer file, keeping a ledger tombstone."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from four_layer_memory import signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore, StoreError


class ForgetCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, text: str, layer: str = "D", **kw) -> Entry:
        return self.store.add(Entry(layer, text, kw.pop("source", "m:1"), **kw))

    def test_forget_removes_the_bullet_but_keeps_the_event(self) -> None:
        keep = self._add("Korpus has 400 chapters.")
        gone = self._add("Expired state: user is at the desk.")
        self.assertIn(gone.entry_id, (self.root / "background" / "archive.md").read_text(encoding="utf-8"))

        report = self.store.forget(gone.entry_id, reason="expired state cleanup")
        self.assertEqual(report["layer"], "D")
        self.assertTrue(report["forgotten"])

        raw = (self.root / "background" / "archive.md").read_text(encoding="utf-8")
        self.assertNotIn(gone.entry_id, raw)
        self.assertIn(keep.entry_id, raw)
        self.assertNotIn("Expired state", self.store.read_layers(["D"])["text"])

        events = [r for r in self.store.ledger_records() if r.get("event") == "forget"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["entry_id"], gone.entry_id)
        self.assertEqual(events[0]["reason"], "expired state cleanup")

    def test_expected_layers_match_after_forget(self) -> None:
        gone = self._add("Temporary experiment.", layer="C")
        self.store.forget(gone.entry_id, reason="experiment over")
        expected = self.store.expected_layers()["C"]
        actual = (self.root / "capabilities" / "index.md").read_text(encoding="utf-8")
        self.assertEqual(expected, actual)
        self.assertTrue(audit_store(self.root)["ok"])

    def test_forget_is_idempotent(self) -> None:
        gone = self._add("Temporary experiment.", layer="C")
        self.store.forget(gone.entry_id)
        again = self.store.forget(gone.entry_id)
        self.assertTrue(again.get("already"))
        events = [r for r in self.store.ledger_records() if r.get("event") == "forget"]
        self.assertEqual(len(events), 1)

    def test_unknown_entry_raises(self) -> None:
        with self.assertRaises(StoreError):
            self.store.forget("deadbeefcafe")

    def test_ledger_stays_intact(self) -> None:
        gone = self._add("Something to drop.")
        self.store.forget(gone.entry_id, reason="cleanup")
        self.assertTrue(self.store.ledger_integrity()["ok"])
        self.assertTrue(self.store.verify_ledger_records(self.store.ledger_records())["ok"])

    def test_cli_forget(self) -> None:
        from four_layer_memory import cli
        gone = self._add("Drop me from layer D.")
        rc = cli.main(["--root", str(self.root), "forget", gone.entry_id, "--reason", "cli test"])
        self.assertEqual(rc, 0)
        raw = (self.root / "background" / "archive.md").read_text(encoding="utf-8")
        self.assertNotIn(gone.entry_id, raw)


if __name__ == "__main__":
    unittest.main()
