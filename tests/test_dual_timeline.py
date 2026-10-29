# liu bai (留白): dual timeline tests. Provenance watermark: liu bai.
"""P1-8: dual timeline (valid_from / valid_to) probes."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import approval, cli, signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore, _ts
from four_layer_memory.watermark import WATERMARK

PAST = "2020-01-01"
PRESENT = "2026-01-01"
LATER = "2099-01-01"


class DualTimelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- validation -------------------------------------------------------
    def test_inverted_window_is_rejected(self) -> None:
        entry = Entry("D", "Backwards claim.", "m:1", valid_from=LATER, valid_to=PAST)
        with self.assertRaises(Exception):
            entry.validate()

    def test_unparsable_timestamp_is_rejected(self) -> None:
        entry = Entry("D", "Bad date.", "m:1", valid_from="not-a-date")
        with self.assertRaises(Exception):
            entry.validate()

    # -- servability ------------------------------------------------------
    def test_historical_claim_is_not_served(self) -> None:
        self.store.add(Entry("D", "Korpus has 388 chapters.", "m:1",
                             valid_from=PAST, valid_to=PRESENT))
        self.store.add(Entry("D", "Corpus has 400 chapters.", "m:2",
                             valid_from=PRESENT))
        fresh = MemoryStore(self.root)
        served = fresh.read_layers(["D"])
        self.assertEqual(len(served["historical"]), 1)
        self.assertEqual(served["future"], [])
        self.assertIn("400 chapters", served["text"])
        self.assertNotIn("388 chapters", served["text"])

    def test_future_claim_is_not_served(self) -> None:
        self.store.add(Entry("D", "Korpus renamed next year.", "m:1", valid_from=LATER))
        fresh = MemoryStore(self.root)
        served = fresh.read_layers(["D"])
        self.assertEqual(len(served["future"]), 1)
        self.assertNotIn("renamed", served["text"])

    def test_search_hides_historical_unless_asked(self) -> None:
        self.store.add(Entry("D", "Korpus has 388 chapters.", "m:1",
                             valid_from=PAST, valid_to=PRESENT))
        fresh = MemoryStore(self.root)
        self.assertEqual(fresh.search("chapters"), [])
        wide = fresh.search("chapters", include_historical=True)
        self.assertEqual(len(wide), 1)
        self.assertEqual(wide[0]["valid_to"], PRESENT)

    # -- persistence ------------------------------------------------------
    def test_window_survives_bullet_round_trip(self) -> None:
        entry = Entry("D", "Korpus has 388 chapters.", "m:1",
                      valid_from=PAST, valid_to=LATER)
        self.store.add(entry)
        body = (self.root / "background" / "archive.md").read_text(encoding="utf-8")
        self.assertIn("`valid_from:", body)
        self.assertIn("`valid_to:", body)
        back = MemoryStore(self.root)._parse_layer("D")[0]
        match = [e for e, _raw in back if e.entry_id == entry.entry_id][0]
        self.assertEqual((match.valid_from, match.valid_to), (PAST, LATER))

    def test_export_import_keeps_windows(self) -> None:
        self.store.add(Entry("D", "Korpus has 388 chapters.", "m:1",
                             valid_from=PAST, valid_to=PRESENT))
        bundle = self.root / "bundle.json"
        self.store.export_bundle(bundle)
        target = Path(tempfile.mkdtemp())
        fresh = MemoryStore(target)
        report = fresh.import_bundle(bundle)
        self.assertEqual(report["imported"], 1)
        served = fresh.read_layers(["D"])
        self.assertEqual(len(served["historical"]), 1)
        self.assertNotIn("388 chapters", served["text"])

    # -- audit ------------------------------------------------------------
    def test_audit_counts_windows(self) -> None:
        self.store.add(Entry("D", "Korpus has 388 chapters.", "m:1",
                             valid_from=PAST, valid_to=PRESENT))
        self.store.add(Entry("D", "Korpus renamed next year.", "m:2", valid_from=LATER))
        report = audit_store(self.root)
        self.assertTrue(report["ok"], report["problems"])
        temporal = report["checks"]["temporal"]
        self.assertEqual(temporal["historical"], 1)
        self.assertEqual(temporal["future"], 1)
        self.assertEqual(temporal["findings"], [])

    # -- cli --------------------------------------------------------------
    def test_cli_accepts_valid_window(self) -> None:
        out = io.StringIO()
        import contextlib
        args = ["--root", str(self.root), "add", "D", "Korpus has 388 chapters.",
                "--source", "m:1", "--valid-from", PAST, "--valid-to", PRESENT]
        with contextlib.redirect_stdout(out):
            cli.main(args)
        sys_out = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertTrue(sys_out["accepted"])
        served = MemoryStore(self.root).read_layers(["D"])
        self.assertEqual(len(served["historical"]), 1)


class WatermarkCase(unittest.TestCase):
    def test_watermark_untouched_by_timeline(self) -> None:
        self.assertEqual(WATERMARK, "liu bai")


if __name__ == "__main__":
    unittest.main()
