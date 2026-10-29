# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""BM25 ranking for typed recall (v0.7.6).

The store's recall is exact-substring first: an entry is a hit when the query
appears in it.  That is precise but brittle - "记忆架构" will not match
"memory architecture" and a query that shares only some words finds nothing.
This module adds the other half of the pair without a dependency or an index:
a BM25 score over the entries that are already in memory (there are hundreds,
not millions, so scoring them per query is cheap and always current).

Tokenisation is deliberately simple and language-neutral:

* Latin/digit runs become words lowercased;
* a CJK run becomes its characters *and* its character bigrams, so "记忆" and
  "架构" both match inside "记忆架构", and word order does not decide recall.

Nothing here mutates a store; ranking is a pure function of the query and the
texts handed to it.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens plus CJK characters and bigrams."""
    low = (text or "").casefold()
    tokens: list[str] = []
    for match in _WORD.finditer(low):
        tokens.append(match.group(0))
    for match in _CJK.finditer(low):
        run = match.group(0)
        tokens.extend(run)
        if len(run) > 1:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def bm25_rank(query: str, docs: list[str], k1: float = K1, b: float = B) -> list[float]:
    """Return one BM25 score per document (0.0 when nothing matches)."""
    if not docs:
        return []
    q_terms = tokenize(query)
    if not q_terms:
        return [0.0] * len(docs)
    counts = [Counter(tokenize(d)) for d in docs]
    lengths = [sum(c.values()) for c in counts]
    n = len(docs)
    avgdl = (sum(lengths) / n) if n else 0.0
    df: Counter[str] = Counter()
    for c in counts:
        df.update(c.keys())
    idf = {term: math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
           for term in set(q_terms)}
    scores: list[float] = []
    for c, dl in zip(counts, lengths):
        score = 0.0
        norm = 1.0 - b + b * (dl / avgdl if avgdl else 0.0)
        for term in q_terms:
            tf = c.get(term, 0)
            if not tf:
                continue
            score += idf[term] * (tf * (k1 + 1.0)) / (tf + k1 * norm)
        scores.append(score)
    return scores
