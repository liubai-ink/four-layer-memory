# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Unit tests for four-layer-memory."""

import json
import tempfile
import unittest

# Deliberately shaped like a key but assembled at runtime so scanners stay quiet.
FAKE_KEY = "sk-" + "abc" * 6
from pathlib import Path

from four_layer_memory import Layer, MemoryStore, Entry, StoreError, Watermark, Action, decide, audit_store
from . import helpers
from four_layer_memory.distill import Candidate, plan
from four_layer_memory.handoff import Handoff


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.store = MemoryStore(self.root, session_id="t1")

    def tearDown(self):
        self.td.cleanup()

    def test_layer_files_created(self):
        for layer in Layer:
            self.assertTrue(self.store.path_for(layer).exists())

    def test_add_requires_source(self):
        with self.assertRaises(StoreError):
            self.store.add(Entry("A", "Always be kind.", authority="user-explicit", evidence="quote",
                             source_event_id="evt-1", approved=True))

    def test_add_rejects_secret(self):
        with self.assertRaises(StoreError):
            self.store.add(Entry("B", "key " + FAKE_KEY, "m1"))

    def test_add_rejects_long_text(self):
        with self.assertRaises(StoreError):
            self.store.add(Entry("D", "x" * 500, "m1"))

    def test_add_and_counts(self):
        helpers.add_a(self.store, "Always report the conclusion first.", "m1", "evt-2")
        self.store.add(Entry("D", "The project started in October.", "m2"))
        self.assertEqual(self.store.state.layer_counts["conventions"], 1)
        self.assertEqual(self.store.state.layer_counts["background"], 1)

    def test_consolidate_state(self):
        st = self.store.consolidate("m00042", 0.41, cooldown_entries=80)
        self.assertEqual(st["last_anchor_ref"], "m00042")
        self.assertEqual(st["cooldown_until_entries"], 80)

    def test_export_bundle_has_watermark(self):
        self.store.add(Entry("C", "Query the corpus with corpus_query.py.", "m3"))
        out = self.store.export_bundle(self.root / "bundle.json")
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["watermark"], "liu bai")

    def test_merge_backup(self):
        helpers.add_a(self.store, "First rule.", "m1", "evt-3")
        helpers.add_a(self.store, "Second rule.", "m2", "evt-4")
        self.assertTrue(any(self.store.versions.iterdir()))


class TriggerTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(decide(0.10), Action.NONE)
        self.assertEqual(decide(0.31, turns_since=1), Action.NONE)
        self.assertEqual(decide(0.31, turns_since=3), Action.CHECK)
        self.assertEqual(decide(0.36), Action.CHECK)
        self.assertEqual(decide(0.39, entries_since=60, cooldown_entries=50), Action.CONSOLIDATE)
        self.assertEqual(decide(0.46), Action.CONSOLIDATE)  # fold only when asked for
        self.assertEqual(decide(0.46, entries_since=1, allow_fold=True), Action.FOLD)
        self.assertEqual(decide(0.96), Action.EMERGENCY)

    def test_custom_watermark(self):
        wm = Watermark(consolidate_at=0.20, fold_at=0.30, emergency_at=0.90)
        self.assertEqual(decide(0.25, entries_since=10, cooldown_entries=5, wm=wm), Action.CONSOLIDATE)


class DistillTest(unittest.TestCase):
    def test_classify_and_plan(self):
        cands = [
            Candidate("Always keep provenance.", "m1"),
            Candidate("Prefer tables over paragraphs.", "m2"),
            Candidate("Run scripts/verify.py before release.", "m3"),
            Candidate("The human likes late-night work.", "m4"),
        ]
        planned = plan(cands)
        self.assertEqual(len(planned["conventions"]), 1)
        self.assertEqual(len(planned["preferences"]), 1)
        self.assertEqual(len(planned["capabilities"]), 1)
        self.assertEqual(len(planned["background"]), 1)


class HandoffTest(unittest.TestCase):
    def test_resume_after_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "handoff.json"
            h = Handoff(p)
            records = [{"ref": "m1"}, {"ref": "m2"}, {"ref": "m3"}]
            self.assertEqual(len(h.resume(records)), 3)
            h.mark_consolidated("m2")
            self.assertEqual(h.resume(records), [{"ref": "m3"}])

    def test_unknown_cursor_replays(self):
        with tempfile.TemporaryDirectory() as td:
            h = Handoff(Path(td) / "h.json")
            h.mark_consolidated("nope")
            self.assertEqual(h.resume([{"ref": "m1"}]), [{"ref": "m1"}])


class AuditTest(unittest.TestCase):
    def test_audit_clean_store(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            helpers.add_a(store, "Always keep provenance.", "m1", "evt-9")
            report = audit_store(td)
            self.assertTrue(report["ok"], report)

    def test_audit_detects_secret(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(td)
            p = store.path_for(Layer.PREFERENCE)
            p.write_text(p.read_text(encoding="utf-8") + "\n- token " + FAKE_KEY + "\n", encoding="utf-8")
            report = audit_store(td)
            self.assertFalse(report["ok"])
            self.assertTrue(any("secret" in x for x in report["problems"]))


if __name__ == "__main__":
    unittest.main()
