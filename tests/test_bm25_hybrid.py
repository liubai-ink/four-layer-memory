# liu bai (留白): BM25 hybrid recall tests. Provenance watermark: liu bai.
"""P2-9: optional BM25 ranking for the query side."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import cli, signing
from four_layer_memory.bm25 import bm25_rank, tokenize
from four_layer_memory.store import Entry, MemoryStore


class TokenizeCase(unittest.TestCase):
    def test_latin_words_are_lowercased(self) -> None:
        self.assertEqual(tokenize("Memory Architecture"), ["memory", "architecture"])

    def test_cjk_single_chars_and_bigrams(self) -> None:
        self.assertEqual(tokenize("记忆架构"), ["记", "忆", "架", "构", "记忆", "忆架", "架构"])

    def test_mixed_script(self) -> None:
        tokens = tokenize("记忆架构 memory")
        self.assertIn("架构", tokens)
        self.assertIn("memory", tokens)

    def test_empty_text(self) -> None:
        self.assertEqual(tokenize("   "), [])


class RankCase(unittest.TestCase):
    def test_relevant_document_scores_higher(self) -> None:
        docs = ["Korpus has 388 chapters.", "The substrate is typed.", "Memory architecture notes."]
        scores = bm25_rank("memory architecture", docs)
        self.assertEqual(len(scores), 3)
        self.assertGreater(scores[2], scores[0])
        self.assertGreater(scores[2], scores[1])

    def test_no_match_scores_zero(self) -> None:
        self.assertEqual(bm25_rank("zzzz", ["alpha beta"]), [0.0])

    def test_empty_query_scores_zero(self) -> None:
        self.assertEqual(bm25_rank("", ["alpha beta"]), [0.0])


class HybridSearchCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        signing.keygen(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _add(self, text: str, **kw) -> Entry:
        return self.store.add(Entry("D", text, kw.pop("source", "m:1"), **kw))

    def test_bigram_query_needs_hybrid(self) -> None:
        self._add("记忆架构是留下白做的。")
        self.assertEqual(self.store.search("架构记忆"), [])
        hits = self.store.search("架构记忆", hybrid=True)
        self.assertEqual(len(hits), 1)
        self.assertIn("score", hits[0])
        self.assertIn("记忆", hits[0]["text"])

    def test_hybrid_ranks_exact_substring_first(self) -> None:
        self._add("纯打字机记录，没有别的。")
        self._add("记忆架构 memory architecture 的记忆架构说明。")
        hits = self.store.search("memory", hybrid=True)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("memory architecture", hits[0]["text"])

    def test_hybrid_skips_revoked_and_expired(self) -> None:
        self._add("记忆架构 revoked twin.", source_event_id="evt-r")
        self._add("记忆架构 expired twin.", lifecycle="expiring",
                  expires_at="2020-01-01T00:00:00")
        self.store.revoke("evt-r")
        hits = self.store.search("记忆架构", hybrid=True)
        self.assertEqual(hits, [])

    def test_hybrid_response_shape(self) -> None:
        self._add("记忆架构 notes.")
        hit = self.store.search("记忆架构", hybrid=True)[0]
        for key in ("layer", "entry_id", "text", "at", "revoked", "matches",
                    "superseded_by", "score"):
            self.assertIn(key, hit)

    def test_cli_search_hybrid(self) -> None:
        self._add("记忆架构 notes.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--root", str(self.root), "search", "架构记忆", "--hybrid"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["hybrid"])
        self.assertEqual(len(payload["hits"]), 1)


if __name__ == "__main__":
    unittest.main()
