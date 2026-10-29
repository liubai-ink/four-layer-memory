# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""``export_bundle`` must have a working inverse: ``init --from-bundle``.

docs/PORTABILITY.md promises that the record travels with the data.  What must
*not* travel is the exporter's approval signatures -- they are bound to its host
key, so a stolen bundle cannot widen layer A anywhere else.  These tests pin
both halves of that promise.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import approval, cli, signing
from four_layer_memory.store import Entry, MemoryStore, StoreError

from .helpers import add_a


def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def new_store(tmp: str, name: str) -> MemoryStore:
    root = Path(tmp) / name
    root.mkdir()
    signing.keygen(str(root))
    approval.ensure_key(str(root))
    return MemoryStore(str(root))


class ImportBundleTest(unittest.TestCase):
    def _source(self, tmp: str) -> tuple[MemoryStore, Path]:
        src = new_store(tmp, "source")
        add_a(src, "Always answer in Chinese.", source="m1", source_event_id="evt-a1")
        src.add(Entry("B", "Prefers tables over prose.", "m2",
                      authority="user-implied", evidence="paraphrase"))
        src.add(Entry("C", "Knows the four-layer CLI.", "m3",
                      authority="observed", evidence="summary",
                      source_event_id="evt-c1"))
        src.add(Entry("C", "An old claim that was retracted.", "m4",
                      authority="observed", evidence="summary",
                      source_event_id="evt-c2"))
        src.revoke("evt-c2")
        bundle = Path(tmp) / "bundle.json"
        src.export_bundle(bundle)
        return src, bundle

    def test_round_trip_rebuilds_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, bundle = self._source(tmp)
            dst = new_store(tmp, "target")
            report = dst.import_bundle(bundle)

            self.assertEqual(4, report["imported"], report)
            self.assertEqual(1, report["reapproved"], report)
            self.assertEqual([], report["revoked_mismatch"], report)
            self.assertEqual(src.revoked_ids(), dst.revoked_ids())

            # Layer A came back approved, but by *this* host, not the exporter:
            # the replay event is an approve and its record is freshly minted.
            blocks, unknown = dst._parse_layer("A")
            self.assertEqual([], unknown)
            self.assertEqual(1, len(blocks))
            a_id = blocks[0][0].entry_id
            records = {r.get("entry_id"): r for r in dst.ledger_records()
                       if r.get("event") in ("write", "approve")}
            self.assertEqual("approve", records[a_id]["event"])
            self.assertTrue(records[a_id]["approval_record"].get("sig"))
            self.assertNotEqual(json.loads(bundle.read_text())["approval_fingerprint"],
                                report["approval_fingerprint"])

            # The revoked claim is a tombstone, not a live entry.
            self.assertEqual(["An old claim that was retracted."],
                             [x["text"] for x in dst.search("retracted",
                                                            include_revoked=True)])
            self.assertEqual([], dst.search("retracted"))
            self.assertEqual([], dst.quarantine_entries())

            code, out = run_cli(["--root", str(dst.root), "audit"])
            report = json.loads(out[out.index("{"):])
            self.assertTrue(report["ok"], report)

    def test_bundle_without_watermark_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _src, bundle = self._source(tmp)
            payload = json.loads(bundle.read_text())
            payload["watermark"] = "someone-else"
            forged = Path(tmp) / "forged.json"
            forged.write_text(json.dumps(payload))
            dst = new_store(tmp, "target")
            with self.assertRaises(StoreError):
                dst.import_bundle(forged)

    def test_tampered_ledger_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _src, bundle = self._source(tmp)
            payload = json.loads(bundle.read_text())
            payload["ledger"][1]["text"] = "silently rewritten claim"
            broken = Path(tmp) / "broken.json"
            broken.write_text(json.dumps(payload))
            dst = new_store(tmp, "target")
            with self.assertRaises(StoreError):
                dst.import_bundle(broken)

    def test_non_empty_target_needs_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, bundle = self._source(tmp)
            with self.assertRaises(StoreError):
                src.import_bundle(bundle)

    def test_cli_init_from_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _src, bundle = self._source(tmp)
            root = str(Path(tmp) / "cli-target")
            code, out = run_cli(["--root", root, "init", "--from-bundle", str(bundle)])
            self.assertEqual(0, code, out)
            status = json.loads(out)
            self.assertEqual(4, status["import"]["imported"])
            self.assertEqual(1, status["import"]["reapproved"])
            self.assertIn("approval_fingerprint", status)

    def test_bundle_keeps_the_provenance_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _src, bundle = self._source(tmp)
            payload = json.loads(bundle.read_text())
            self.assertIn("liubai", payload["note"])
            self.assertIn("liu bai", payload["note"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
