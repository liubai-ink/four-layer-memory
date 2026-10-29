# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""v0.7.7: a store that quotes itself, or silently rewrites its evidence.

Two failure modes that no structural check can see:

* the served entries drift towards ``observed`` / ``inferred`` rows that the
  agent produced itself (the store ends up quoting itself), and
* a ``summary`` / ``paraphrase`` without a ``source_event_id`` replaces the
  original words: the rewrite *is* the evidence now, and the original cannot be
  recovered (\"改写即失真\").

``MemoryStore.authority_mix`` / ``provenance_coverage`` measure both; the audit
reports them; ``cmd_add`` warns (or refuses, under ``FLM_REQUIRE_ANCHOR=1``).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import cli, signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore


def write(store: MemoryStore, layer: str, text: str, **kwargs) -> Entry:
    kwargs.setdefault("authority", "user-explicit")
    kwargs.setdefault("evidence", "paraphrase")
    return store.add(Entry(layer, text, "session:m1", **kwargs))


class AuthorityMixCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mix_counts_served_entries(self) -> None:
        write(self.store, "B", "Prefers tables over prose.",
              source_event_id="evt-1")
        write(self.store, "D", "The corpus has 388 chapters.",
              authority="observed", evidence="summary", source_event_id="evt-2")
        mix = self.store.authority_mix()
        self.assertEqual(2, mix["total"])
        self.assertEqual({"observed": 1, "user-explicit": 1}, mix["authority"])
        self.assertEqual({"paraphrase": 1, "summary": 1}, mix["evidence"])
        self.assertEqual(0, mix["derived"])

    def test_derived_share_counts_inference(self) -> None:
        write(self.store, "D", "Probably prefers morning sessions.",
              authority="inferred", evidence="inference", source_event_id="evt-1")
        write(self.store, "D", "Guessed runtime is around 3 seconds.",
              authority="model-generated", evidence="inference",
              source_event_id="evt-2")
        mix = self.store.authority_mix()
        self.assertEqual(2, mix["derived"])
        self.assertEqual(1.0, mix["derived_share"])

    def test_empty_store_is_zero_not_crash(self) -> None:
        mix = self.store.authority_mix()
        self.assertEqual(0, mix["total"])
        self.assertEqual(0.0, mix["derived_share"])
        self.assertEqual(1.0, self.store.provenance_coverage()["coverage"])


class ProvenanceCoverageCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rewrite_without_anchor_has_no_coverage(self) -> None:
        entry = write(self.store, "B", "Prefers short answers.")
        cov = self.store.provenance_coverage()
        self.assertEqual(1, cov["rewrites"])
        self.assertEqual(0, cov["anchored"])
        self.assertEqual(0.0, cov["coverage"])
        self.assertEqual(1, cov["unanchored"])
        self.assertIn(entry.entry_id, cov["unanchored_sample"])

    def test_anchor_restores_coverage(self) -> None:
        write(self.store, "B", "Prefers short answers.", source_event_id="msg:42")
        cov = self.store.provenance_coverage()
        self.assertEqual(1, cov["anchored"])
        self.assertEqual(1.0, cov["coverage"])
        self.assertEqual([], cov["unanchored_sample"])

    def test_supersedes_counts_as_an_anchor(self) -> None:
        old = write(self.store, "B", "Prefers long answers.")
        write(self.store, "B", "Prefers short answers.", supersedes=old.entry_id)
        cov = self.store.provenance_coverage()
        self.assertEqual(2, cov["rewrites"])
        self.assertEqual(1, cov["anchored"])
        self.assertEqual(0.5, cov["coverage"])

    def test_quotes_are_reported_separately(self) -> None:
        write(self.store, "B", "He said: keep it short.", evidence="quote",
              source_event_id="msg:9")
        cov = self.store.provenance_coverage()
        self.assertEqual(0, cov["rewrites"])
        self.assertEqual(1, cov["quotes"])
        self.assertEqual(1, cov["quotes_anchored"])


class AuditReportingCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_audit_always_reports_both_checks(self) -> None:
        write(self.store, "B", "Prefers short answers.", source_event_id="msg:1")
        report = audit_store(self.root)
        for name in ("authority", "provenance"):
            self.assertIn(name, report["checks"])
            self.assertTrue(report["checks"][name]["ok"])
        self.assertTrue(report["ok"])

    def test_unanchored_rewrites_are_reported_as_debt(self) -> None:
        write(self.store, "B", "Prefers short answers.")
        prov = audit_store(self.root)["checks"]["provenance"]
        self.assertEqual(1, prov["unanchored"])
        self.assertFalse(prov["meets_target"])
        self.assertEqual(0.9, prov["target"])
        # Legacy debt must not fail the build (new writes are guarded instead).
        self.assertTrue(prov["ok"])

    def test_derived_share_over_limit_is_a_problem(self) -> None:
        for i in range(18):
            write(self.store, "C", f"Inferred capability {i}.",
                  authority="inferred", evidence="inference",
                  source_event_id=f"msg:i{i}")
        for i in range(3):
            write(self.store, "C", f"Observed capability {i}.",
                  authority="observed", source_event_id=f"msg:o{i}")
        report = audit_store(self.root)
        authority = report["checks"]["authority"]
        self.assertEqual(21, authority["total"])
        self.assertEqual(21, authority["claims"]["total"])
        self.assertTrue(authority["judged"])
        self.assertEqual(0.8571, authority["derived_share"])
        self.assertFalse(authority["ok"])
        self.assertIn("authority: derived_share_high 18/21", report["problems"])

    def test_background_layer_is_not_judged(self) -> None:
        # Layer D is a chronicle of facts; its inferences do not drift the claims.
        for i in range(25):
            write(self.store, "D", f"Background note {i}.",
                  authority="inferred", evidence="inference",
                  source_event_id=f"msg:b{i}")
        authority = audit_store(self.root)["checks"]["authority"]
        self.assertEqual(25, authority["total"])
        self.assertEqual(0, authority["claims"]["total"])
        self.assertFalse(authority["judged"])
        self.assertTrue(authority["ok"])


class AnchorGuardCase(unittest.TestCase):
    """cmd_add is the write gate: a rewrite must point back at its source."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("FLM_REQUIRE_ANCHOR", None)

    def run_add(self, *extra: str) -> tuple[int, str, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["--root", str(self.root), "add", "B", "Prefers short answers.",
                "--source", "session:m1", "--authority", "user-explicit",
                "--evidence", "paraphrase", "--approved", *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), ""

    def test_unanchored_rewrite_warns_but_is_written(self) -> None:
        code, out, err, _ = self.run_add()
        self.assertEqual(0, code)
        self.assertIn("warning: rewrite without an anchor", err)
        self.assertTrue(json.loads(out)["accepted"])

    def test_anchor_silences_the_warning(self) -> None:
        code, _, err, _ = self.run_add("--source-event", "msg:42")
        self.assertEqual(0, code)
        self.assertEqual("", err)

    def test_require_anchor_refuses_the_write(self) -> None:
        os.environ["FLM_REQUIRE_ANCHOR"] = "1"
        code, out, err, _ = self.run_add()
        self.assertEqual(2, code)
        self.assertIn("error: rewrite without an anchor", err)
        self.assertEqual("", out)
        self.assertEqual([], MemoryStore(self.root, recover=False)._servable())


if __name__ == "__main__":
    unittest.main()
