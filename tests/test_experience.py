# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""R052: experience-following is why a wrong memory spreads.

Retrieving a stored experience and copying it is how an inaccurate past gets
amplified.  Two moves are pinned here: an entry can be declared *experience*
(not fact), and an experience that failed when it was used stops being served.
The verdict is a ledger event: the claim stays auditable, and facts -- which are
decided at the write path -- are never scored by convenience.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import cli, signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore, StoreError


def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


class ExperienceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _experience(self, text: str, **kw) -> Entry:
        return self.store.add(
            Entry("D", text, kw.pop("source", "m:1"), kind="experience", **kw))

    # -- the declaration itself --------------------------------------------
    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(StoreError) as ctx:
            Entry("D", "Some claim.", "m:1", kind="opinion").validate()
        self.assertIn("unknown kind", str(ctx.exception))

    def test_kind_round_trips_through_the_layer_file(self) -> None:
        self._experience("Deploy the runner before the tests.")
        raw = (self.root / "background" / "archive.md").read_text(encoding="utf-8")
        self.assertIn("`kind: experience`", raw)
        pairs, _unknown = MemoryStore.parse_layer_text(raw)
        self.assertEqual(["experience"], [entry.kind for entry, _lines in pairs])

    # -- a failed experience stops being copied ----------------------------
    def test_failed_experience_is_not_served(self) -> None:
        bad = self._experience("Re-run consolidate before every test.")
        self._experience("Run the unit tests first.")
        self.store.record_outcome([bad.entry_id], ok=False, reason="runner broke")

        served = self.store.read_layers(["D"])
        self.assertNotIn("consolidate", served["text"])
        self.assertIn("unit tests first", served["text"])
        self.assertEqual([bad.entry_id], served["failed"])

        self.assertEqual([], self.store.search("consolidate"))
        hits = self.store.search("consolidate", include_failed=True)
        self.assertEqual(1, len(hits))
        self.assertEqual("failed", hits[0]["outcome"])

    def test_ok_verdict_keeps_the_experience(self) -> None:
        entry = self._experience("Run the unit tests first.")
        self.store.record_outcome([entry.entry_id], ok=True)
        self.assertIn("unit tests first",
                      self.store.read_layers(["D"])["text"])
        hits = self.store.search("unit tests")
        self.assertEqual("ok", hits[0]["outcome"])

    def test_a_later_verdict_wins(self) -> None:
        entry = self._experience("A reasonable habit.")
        self.store.record_outcome([entry.entry_id], ok=False)
        self.assertEqual([], self.store.search("habit"))
        self.store.record_outcome([entry.entry_id], ok=True)
        self.assertEqual(1, len(self.store.search("habit")))

    # -- measurement --------------------------------------------------------
    def test_stats_count_labelled_experience(self) -> None:
        one = self._experience("First habit.")
        self._experience("Second habit.")
        self.store.record_outcome([one.entry_id], ok=False)
        stats = self.store.experience_stats()
        self.assertEqual(2, stats["total"])
        self.assertEqual(1, stats["labelled"])
        self.assertEqual(1, stats["failed"])
        self.assertEqual(0.5, stats["label_coverage"])

    def test_facts_are_never_scored(self) -> None:
        fact = self.store.add(Entry("D", "Korpus has 400 chapters.", "m:1"))
        with self.assertRaises(StoreError) as ctx:
            self.store.record_outcome([fact.entry_id], ok=False)
        self.assertIn("write path", str(ctx.exception))
        self.assertEqual(0, self.store.experience_stats()["facts_scored"])
        self.assertIn("400 chapters", self.store.read_layers(["D"])["text"])

    def test_unknown_id_is_rejected(self) -> None:
        with self.assertRaises(StoreError) as ctx:
            self.store.record_outcome(["deadbeefcafe"], ok=True)
        self.assertIn("unknown entry id", str(ctx.exception))

    def test_empty_ids_are_rejected(self) -> None:
        with self.assertRaises(StoreError):
            self.store.record_outcome([], ok=True)

    # -- audit reports the debt without failing the run --------------------
    def test_audit_reports_unlabelled_experience_as_debt(self) -> None:
        self._experience("A habit nobody scored.")
        report = audit_store(self.root)
        self.assertTrue(report["ok"], report)
        check = report["checks"]["experience"]
        self.assertEqual(1, check["total"])
        self.assertEqual(0, check["labelled"])
        self.assertFalse(check["meets_target"])
        self.assertEqual(["experience_unlabelled"],
                         [finding["code"] for finding in check["findings"]])

    def test_audit_is_quiet_once_everything_is_labelled(self) -> None:
        entry = self._experience("A habit that worked.")
        self.store.record_outcome([entry.entry_id], ok=True)
        report = audit_store(self.root)
        check = report["checks"]["experience"]
        self.assertTrue(check["meets_target"])
        self.assertEqual([], check["findings"])
        self.assertTrue(report["ok"])

    def test_outcome_events_do_not_break_the_ledger_chain(self) -> None:
        entry = self._experience("A habit.")
        self.store.record_outcome([entry.entry_id], ok=False)
        MemoryStore(self.root)  # reopening verifies the hash chain
        self.assertTrue(audit_store(self.root)["ok"])


class ExperienceCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_records_and_gates_an_experience(self) -> None:
        code, out = run_cli(["--root", str(self.root), "add", "D",
                             "Use the runbook before deploying.",
                             "--source", "test:cli",
                             "--kind", "experience", "--source-event", "evt-1"])
        self.assertEqual(0, code, out)
        entry_id = json.loads(out)["entry_id"]

        code, out = run_cli(["--root", str(self.root), "record-outcome",
                             entry_id, "--failed", "--reason", "wrong order"])
        self.assertEqual(0, code, out)
        self.assertFalse(json.loads(out)["ok"])

        code, out = run_cli(["--root", str(self.root), "search", "runbook"])
        self.assertEqual(0, code, out)
        self.assertEqual([], json.loads(out)["hits"])

        code, out = run_cli(["--root", str(self.root), "search", "runbook",
                             "--include-failed"])
        self.assertEqual(1, len(json.loads(out)["hits"]))


if __name__ == "__main__":
    unittest.main()
