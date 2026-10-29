# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Regression tests for the second review round (v0.7.8).

Each case pins a defect that shipped in ``b557148`` and was fixed afterwards:
a bundle resurrecting a failed experience, the review queue not travelling,
``read_layers`` hiding failed text with no way to audit it, ``approve()``
laundering a candidate's authority/evidence, and MCP having no way to score
experience at all.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from four_layer_memory import approval  # noqa: E402
from four_layer_memory import mcp  # noqa: E402
from four_layer_memory.store import Entry, MemoryStore, StoreError  # noqa: E402


def fresh(root: Path) -> MemoryStore:
    return MemoryStore(root)


class BundleRoundTripKeepsVerdict(unittest.TestCase):
    def test_failed_experience_does_not_resurrect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = fresh(base / "src")
            entry = Entry(layer="D", text="Deploy from memory alone.",
                          source="m:bad", authority="observed",
                          evidence="quote", kind="experience")
            src.add(entry)
            src.record_outcome([entry.entry_id], ok=False, reason="deploy broke")
            self.assertEqual([entry.entry_id], src.read_layers(["D"])["failed"])

            bundle = src.export_bundle(base / "b.bundle")
            dst = fresh(base / "dst")
            report = dst.import_bundle(bundle, trust=True)
            self.assertGreaterEqual(report["outcomes_replayed"], 1)
            served = dst.read_layers(["D"])
            self.assertNotIn(entry.text, served["text"])
            self.assertEqual([entry.entry_id], served["failed"])
            self.assertEqual([], dst.search("Deploy from memory alone."))


class ReviewQueueTravels(unittest.TestCase):
    def test_pending_claim_is_carried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = fresh(base / "src")
            parked = Entry(layer="A", text="Never deploy on Friday.",
                           source="m:p", authority="observed", evidence="summary")
            src.add(parked)
            self.assertEqual(1, len(src.quarantine_entries()))

            bundle = src.export_bundle(base / "b.bundle")
            dst = fresh(base / "dst")
            report = dst.import_bundle(bundle)
            pending = dst.quarantine_entries()
            self.assertEqual(1, len(pending))
            self.assertEqual(parked.entry_id, pending[0]["entry_id"])
            self.assertIn(parked.entry_id, report["needs_review"])
            self.assertEqual(0, report["imported"])


class FailedTextIsAuditable(unittest.TestCase):
    def test_read_layers_include_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = fresh(Path(tmp))
            entry = Entry(layer="D", text="Trial run went sideways.",
                          source="m:x", authority="observed",
                          evidence="quote", kind="experience")
            store.add(entry)
            store.record_outcome([entry.entry_id], ok=False)
            default = store.read_layers(["D"])
            self.assertNotIn(entry.text, default["text"])
            audited = store.read_layers(["D"], include_failed=True)
            self.assertIn(entry.text, audited["text"])
            block = [b for b in audited["blocks"] if b["entry_id"] == entry.entry_id][0]
            self.assertTrue(block["failed"])


class ApproveDoesNotLaunderProvenance(unittest.TestCase):
    def test_weak_candidate_stays_unapproved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = fresh(Path(tmp))
            entry = Entry(layer="B", text="I prefer trains to planes.",
                          source="m:1", authority="observed",
                          evidence="paraphrase", source_event_id="evt-1")
            store.add(entry)
            self.assertEqual(1, len(store.quarantine_entries()))

            record = approval.mint(store.root, entry_id=entry.entry_id,
                                   text=entry.text, source_event_id="evt-1",
                                   approver="test")
            with self.assertRaises(StoreError):
                store.approve(entry.entry_id, record, approver="test")
            served = store.read_layers(["B"])
            self.assertNotIn(entry.text, served["text"])
            self.assertEqual(1, len(store.quarantine_entries()))


class McpCanScoreExperience(unittest.TestCase):
    def test_handler_registered_and_works(self) -> None:
        self.assertIn("memory_record_outcome", mcp.HANDLERS)
        self.assertIn("memory_record_outcome",
                      [tool["name"] for tool in mcp.TOOLS])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            previous = os.environ.get("FOUR_LAYER_MEMORY_ROOT")
            os.environ["FOUR_LAYER_MEMORY_ROOT"] = str(root)
            try:
                added = mcp.tool_add({"layer": "D", "text": "Ping staging first.",
                                      "source": "mcp:1", "authority": "observed",
                                      "evidence": "quote", "kind": "experience"})
                self.assertEqual("experience", added["kind"])
                reply = mcp.tool_record_outcome({"entry_ids": added["entry_id"],
                                                 "failed": True,
                                                 "reason": "timeout"})
                self.assertFalse(reply["ok"])
                self.assertEqual([added["entry_id"]], reply["entry_ids"])
                loaded = mcp.tool_load({"layers": ["D"]})
                self.assertIn(added["entry_id"], loaded["failed"])
                self.assertNotIn("Ping staging first.", loaded["text"])
            finally:
                if previous is None:
                    os.environ.pop("FOUR_LAYER_MEMORY_ROOT", None)
                else:
                    os.environ["FOUR_LAYER_MEMORY_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
