# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Signing tests: HMAC chain, forgery with recomputed hashes, Ed25519 option."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import signing
from four_layer_memory.audit import audit_store
from four_layer_memory.store import Entry, MemoryStore
import helpers


class SigningCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="flm-sign-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _forge_chain(self, store: MemoryStore, mutate_text: bool) -> None:
        records = store.ledger_records()
        prev = ""
        forged = []
        for rec in records:
            body = {k: v for k, v in rec.items() if k not in ("hash", "sig", "sig_alg")}
            if mutate_text and body.get("event") == "write":
                body["text"] = "forged entry"
            body["prev"] = prev
            body["hash"] = store._sha(store._canonical(body)) if hasattr(store, "_sha") else None
            forged.append(body)
            prev = body["hash"]
        # recompute hashes without the store helper fallback
        from four_layer_memory.store import _canonical, _sha

        prev = ""
        out = []
        for body in forged:
            body.pop("hash", None)
            body["prev"] = prev
            body["hash"] = _sha(_canonical(body))
            prev = body["hash"]
            out.append(body)
        store.ledger_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                      for r in out) + "\n",
            encoding="utf-8",
        )

    def test_hmac_keygen_sign_and_tamper(self) -> None:
        info = signing.keygen(self.tmp)
        self.assertTrue(info["created"])
        self.assertEqual(signing.algorithm(self.tmp), signing.HMAC)
        self.assertFalse(signing.keygen(self.tmp)["created"])

        rec = {"event": "write", "text": "hello"}
        signed = signing.sign(self.tmp, rec)
        self.assertTrue(signing.verify(self.tmp, signed))
        signed["text"] = "hello (edited)"
        self.assertFalse(signing.verify(self.tmp, signed))

    def test_signed_ledger_is_intact(self) -> None:
        signing.keygen(self.tmp)
        store = MemoryStore(self.tmp)
        store.add(Entry("B", "Prefers short summaries.", "msg:1",
                    authority="user-explicit", evidence="quote"))
        store.add(Entry("D", "The corpus has 388 chapters.", "msg:2"))
        integrity = store.ledger_integrity()
        self.assertTrue(integrity["ok"])
        self.assertEqual(integrity["signed"], signing.HMAC)
        self.assertTrue(audit_store(self.tmp)["ok"])

    def test_recomputed_chain_without_signature_is_rejected(self) -> None:
        signing.keygen(self.tmp)
        store = MemoryStore(self.tmp)
        store.add(Entry("B", "Prefers tables.", "msg:1",
                    authority="user-explicit", evidence="quote"))
        self._forge_chain(store, mutate_text=True)

        integrity = store.ledger_integrity()
        self.assertFalse(integrity["ok"])
        self.assertIn("signature", integrity["reason"])
        self.assertFalse(audit_store(self.tmp)["ok"])

    def test_ed25519_roundtrip_when_available(self) -> None:
        try:
            import cryptography  # noqa: F401
        except Exception:
            self.skipTest("cryptography not installed")
        info = signing.keygen(self.tmp, alg=signing.ED25519)
        self.assertTrue(info["created"])
        self.assertEqual(signing.algorithm(self.tmp), signing.ED25519)
        self.assertEqual(len(signing.public_key_hex(self.tmp)), 64)

        store = MemoryStore(self.tmp)
        helpers.add_a(store, "Always cite the source event.", "msg:3", "evt-1")
        self.assertTrue(store.ledger_integrity()["ok"])
        self.assertEqual(store.ledger_integrity()["signed"], signing.ED25519)

        self._forge_chain(store, mutate_text=True)
        self.assertFalse(store.ledger_integrity()["ok"])

    def test_unsigned_store_still_works(self) -> None:
        store = MemoryStore(self.tmp)
        store.add(Entry("D", "Fact without signing.", "msg:4"))
        integrity = store.ledger_integrity()
        self.assertTrue(integrity["ok"])
        self.assertEqual(integrity["signed"], "unsigned")


if __name__ == "__main__":
    unittest.main()
