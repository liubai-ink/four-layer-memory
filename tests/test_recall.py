# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Typed recall (v0.7): search returns entries with authority and revocation state."""

import tempfile
import unittest

from four_layer_memory import Entry, MemoryStore


def _store() -> MemoryStore:
    return MemoryStore(tempfile.mkdtemp())


class RecallTest(unittest.TestCase):
    def test_search_returns_typed_entries(self):
        store = _store()
        entry = store.add(
            Entry(
                layer="B",
                text="比较类问题用表格，结论在前。",
                authority="user-implied",
                evidence="paraphrase",
                source="m00007",
                at="2026-10-26",
            )
        )
        hits = store.search("表格")
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit["entry_id"], entry.entry_id)
        self.assertEqual(hit["layer"], "B")
        self.assertEqual(hit["authority"], "user-implied")
        self.assertEqual(hit["evidence"], "paraphrase")
        self.assertFalse(hit["revoked"])

    def test_search_is_not_fuzzy_by_accident(self):
        store = _store()
        store.add(
            Entry(
                layer="B",
                text="比较类问题用表格。",
                authority="user-implied",
                evidence="paraphrase",
                source="m1",
            )
        )
        self.assertEqual(store.search("不存在的词"), [])

    def test_search_hides_revoked_entries(self):
        store = _store()
        source = "session:test:1"
        store.add(
            Entry(
                layer="B",
                text="旧偏好：长文用段落。",
                authority="user-implied",
                evidence="paraphrase",
                source="s",
                source_event_id=source,
                at="2026-10-01",
            )
        )
        store.revoke(source)
        self.assertEqual(store.search("段落"), [])
        kept = store.search("段落", include_revoked=True)
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["revoked"])

    def test_search_layer_filter_and_limit(self):
        store = _store()
        store.add(
            Entry(layer="B", text="偏好：表格优先。", authority="user-implied",
                  evidence="paraphrase", source="m1")
        )
        store.add(
            Entry(layer="D", text="背景：表格是上周定的。", authority="observed",
                  evidence="summary", source="m2")
        )
        self.assertEqual(len(store.search("表格")), 2)
        self.assertEqual(len(store.search("表格", layer="D")), 1)
        self.assertEqual(len(store.search("表格", limit=1)), 1)

    def test_search_unknown_layer_raises(self):
        store = _store()
        with self.assertRaises(KeyError):
            store.search("x", layer="Z")


if __name__ == "__main__":
    unittest.main()
