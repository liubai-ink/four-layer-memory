# liu bai (留白): write-side merge tests. Provenance watermark: liu bai.
"""P2-10: supersede (write-side merge) + explicit NOOP probes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from four_layer_memory import signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore, StoreError


class SupersedeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, text: str, **kw) -> Entry:
        return self.store.add(Entry("D", text, kw.pop("source", "m:1"), **kw))

    # -- the claim that was replaced is no longer served -------------------
    def test_replaced_claim_is_hidden_but_kept(self) -> None:
        old = self._add("Korpus has 388 chapters.")
        new = self._add("Korpus has 400 chapters.", supersedes=old.entry_id)
        served = self.store.read_layers(["D"])
        self.assertIn("400 chapters", served["text"])
        self.assertNotIn("388 chapters", served["text"])
        self.assertEqual(served["superseded"], [old.entry_id])
        # it is still in the ledger, reachable historically
        old_hits = self.store.search("388 chapters", include_historical=True)
        self.assertEqual(len(old_hits), 1)
        self.assertEqual(old_hits[0]["superseded_by"], new.entry_id)
        self.assertEqual(self.store.search("388 chapters"), [])

    def test_layer_files_keep_both_bullets(self) -> None:
        old = self._add("First wording.")
        self._add("Second wording.", supersedes=old.entry_id)
        raw = (self.root / "background" / "archive.md").read_text(encoding="utf-8")
        self.assertIn("First wording.", raw)
        self.assertIn("Second wording.", raw)
        self.assertIn(f"`supersedes: {old.entry_id}`", raw)

    # -- merge requires a real target --------------------------------------
    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(StoreError) as ctx:
            self._add("Nothing to replace.", supersedes="deadbeefcafe")
        self.assertIn("no such entry", str(ctx.exception))

    def test_self_supersede_is_rejected(self) -> None:
        entry = Entry("D", "Self reference.", "m:1")
        entry.validate()
        entry.supersedes = entry.entry_id
        with self.assertRaises(StoreError):
            entry.validate()

    # -- revoking the successor brings the old claim back ------------------
    def test_revoking_successor_restores_predecessor(self) -> None:
        old = self._add("Original timeline.")
        self._add("Rewritten timeline.", supersedes=old.entry_id,
                  source_event_id="evt-new")
        served = self.store.read_layers(["D"])
        self.assertNotIn("Original timeline.", served["text"])
        report = self.store.revoke("evt-new")
        self.assertEqual(len(report["revoked"]), 1)
        served = self.store.read_layers(["D"])
        self.assertIn("Original timeline.", served["text"])
        self.assertEqual(served["superseded"], [])

    def test_expired_successor_restores_predecessor(self) -> None:
        old = self._add("Stable claim.")
        self._add("Temporary claim.", supersedes=old.entry_id,
                  lifecycle="expiring", expires_at="2020-01-01T00:00:00")
        served = self.store.read_layers(["D"])
        self.assertIn("Stable claim.", served["text"])
        self.assertEqual(served["superseded"], [])

    # -- audit sees the chain ---------------------------------------------
    def test_audit_reports_supersede_chain(self) -> None:
        old = self._add("One.")
        self._add("Two.", supersedes=old.entry_id)
        report = audit_store(self.root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["supersede"]["chains"], 1)
        self.assertEqual(report["checks"]["supersede"]["findings"], [])

    # -- the quarantine -> approve path keeps the claim's metadata ---------
    def test_approval_channel_keeps_supersedes_and_window(self) -> None:
        """The ledger is append-only: whatever the approve rebuild drops is lost.

        A claim that arrives through quarantine (admission rejected it on the
        write side) and is then approved by the host must keep its supersedes
        link and validity window; otherwise the old claim stays in service.
        """
        import contextlib
        import io

        from four_layer_memory import cli

        from . import helpers

        old = helpers.add_a(self.store, "I live in the dorm.", source="m:1",
                            source_event_id="evt-1")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--root", str(self.root), "add", "A", "I moved home.",
                             "--source", "m:2", "--authority", "user-explicit",
                             "--evidence", "quote", "--source-event", "evt-2",
                             "--supersedes", old.entry_id,
                             "--valid-from", "2026-01-01", "--valid-to", "2099-01-01",
                             "--approved"])
        self.assertEqual(0, code, buf.getvalue())

        served = self.store.read_layers(["A"])
        self.assertIn("moved home", served["text"])
        self.assertNotIn("live in the dorm", served["text"])
        self.assertEqual([old.entry_id], served["superseded"])
        record = served["blocks"][0]["record"]
        self.assertEqual(old.entry_id, record["supersedes"])
        self.assertEqual("2099-01-01", record["valid_to"])

        raw = (self.root / "conventions.md").read_text(encoding="utf-8")
        self.assertIn(f"`supersedes: {old.entry_id}`", raw)

    # -- NOOP is a recorded decision, not a write --------------------------
    def test_noop_goes_to_ledger_only(self) -> None:
        before = self.store.read_layers(["B", "C", "D"])["text"]
        report = self.store.record_noop(
            Entry("B", "Same thing said again.", "m:9"), reason="duplicate claim")
        self.assertTrue(report["noop"])
        self.assertEqual(report["layer"], "B")
        after = self.store.read_layers(["B", "C", "D"])["text"]
        self.assertEqual(before, after)
        tail = [r for r in self.store.ledger_records() if r.get("event") == "noop"]
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0]["reason"], "duplicate claim")
        self.assertEqual(tail[0]["entry_id"], report["entry_id"])
        self.assertTrue(audit_store(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()
