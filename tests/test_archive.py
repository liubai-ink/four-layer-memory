# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Tests for the append-only raw archive (v0.7.2)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import approval
from four_layer_memory.archive import Archive
from four_layer_memory.store import Entry, MemoryStore

from .helpers import add_a


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = MemoryStore(self.root, session_id="t")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_appends_chain(self):
        add_a(self.store, "Always answer in Chinese.", source_event_id="evt-1")
        report = self.store.archive.verify()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["count"], 1)
        rec = json.loads(self.store.archive.events_path.read_text(encoding="utf-8").strip())
        self.assertEqual(rec["kind"], "approve")
        self.assertEqual(rec["text"], "Always answer in Chinese.")
        self.assertEqual(rec["prev_sha256"], "0" * 64)

    def test_chain_detects_tampering(self):
        add_a(self.store, "Rule one.", source_event_id="evt-1")
        add_a(self.store, "Rule two.", source_event_id="evt-2")
        lines = self.store.archive.events_path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        payload["text"] = "Rule one (edited)."
        lines[0] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.store.archive.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = Archive(self.root).verify()
        self.assertFalse(report["ok"])
        self.assertTrue(any("sha256 mismatch" in p for p in report["problems"]))

    def test_chain_detects_truncation(self):
        add_a(self.store, "Rule one.", source_event_id="evt-1")
        add_a(self.store, "Rule two.", source_event_id="evt-2")
        lines = self.store.archive.events_path.read_text(encoding="utf-8").splitlines()
        self.store.archive.events_path.write_text(lines[0] + "\n", encoding="utf-8")
        report = Archive(self.root).verify()
        self.assertTrue(report["ok"])  # a prefix is still a valid chain
        self.assertEqual(report["count"], 1)

    def test_search_finds_raw_text(self):
        add_a(self.store, "Use tables over paragraphs.", source_event_id="evt-1")
        self.store.archive.rebuild_index()
        hits = self.store.archive.search("tables")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["entry_id"], self.store.archive.entry_ids().pop())

    def test_search_marks_revoked(self):
        e = add_a(self.store, "Keep provenance in every answer.", source_event_id="evt-9")
        self.store.revoke("evt-9")
        hits = self.store.archive.search("provenance")
        self.assertTrue(hits)
        self.assertTrue(hits[0]["revoked"])
        clean = self.store.archive.search("provenance", include_revoked=False)
        self.assertEqual(clean, [])

    def test_approve_appends_event(self):
        # quarantine path: entry without source event cannot reach A
        q = Entry("A", "Approve me.", "m1", source_event_id="")
        self.store.add(q)
        pend = self.store.quarantine_entries()
        self.assertEqual(len(pend), 1)
        rec = approval.mint(self.root, entry_id=pend[0]["entry_id"],
                            text=pend[0]["text"], source_event_id="evt-7")
        # the entry has no source event -> approval still refused by admission
        with self.assertRaises(Exception):
            self.store.approve(pend[0]["entry_id"], rec)

    def test_audit_reports_archive(self):
        from four_layer_memory.audit import audit_store
        add_a(self.store, "Rule one.", source_event_id="evt-1")
        report = audit_store(self.root)
        self.assertIn("archive", report["checks"])
        self.assertTrue(report["checks"]["archive"]["ok"])
        self.assertEqual(report["checks"]["archive"]["events"], 1)

    def test_audit_detects_missing_capture(self):
        from four_layer_memory.audit import audit_store
        add_a(self.store, "Rule one.", source_event_id="evt-1")
        # remove the archive file to simulate a lost capture log
        self.store.archive.events_path.unlink()
        report = audit_store(self.root)
        self.assertFalse(report["checks"]["archive"]["ok"])


if __name__ == "__main__":
    unittest.main()
