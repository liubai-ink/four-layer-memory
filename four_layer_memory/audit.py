# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Invariants an on-disk memory store should satisfy.

Audit reports three checks separately, because they make different claims:
  structural   -- files exist, provenance is present, counters agree
  content      -- no credential-shaped strings or absolute user paths
  authorization-- no layer-A entry lacks user-explicit, quoted authority
A single "pass" flag would conflate them, so ``ok`` requires all three to pass
but each result is reported on its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import admission
from .layers import Layer, spec
from .store import MemoryStore
from .watermark import WATERMARK

_BS = chr(92)
FORBIDDEN_PATH = re.compile("[A-Za-z]:" + _BS + _BS + "Users" + _BS + _BS + "|/home/[^/]+/")
SECRET = re.compile("sk-" + "[A-Za-z0-9_-]{16,}")


def audit_store(root: str | Path) -> dict:
    root = Path(root)
    store = MemoryStore(root)

    structural: list[str] = []
    content: list[str] = []
    authorization: list[str] = []
    ledger_problems: list[str] = []

    total = 0
    for layer in Layer:
        p = store.path_for(layer)
        if not p.exists():
            structural.append(f"missing layer file: {p.name}")
            continue
        text = p.read_text(encoding="utf-8")
        if WATERMARK not in text and len(text.strip()) > 40:
            structural.append(f"watermark missing: {p.name}")
        if SECRET.search(text):
            content.append(f"possible secret in {p.name}")
        if FORBIDDEN_PATH.search(text):
            content.append(f"absolute user path in {p.name}")
        total += text.count("\n- ")

    state = store.state
    counted = sum(state.layer_counts.values())
    if total and counted != total:
        structural.append(f"state counters disagree: counted={counted} on_disk={total}")

    # Authorization: replay the ledger and verify every layer-A write was admitted.
    bad_a = 0
    revoked = 0
    for rec in store.ledger_records():
        if rec.get("event") == "revoke":
            revoked += len(rec.get("entry_ids") or [])
            continue
        if rec.get("event") != "write":
            continue
        decision = admission.decide(
            rec.get("layer", "D"), rec.get("authority"), rec.get("evidence")
        )
        if not decision.accept:
            bad_a += 1
    if bad_a:
        authorization.append(f"{bad_a} layer-A entries lack explicit quoted authority")

    ledger = store.ledger_integrity()
    if not ledger['ok']:
        ledger_problems.append(
            "ledger chain broken at record " + str(ledger.get('at')) + ": " + str(ledger.get('reason'))
        )

    quarantined = store.quarantined()
    ok = not structural and not content and not authorization and not ledger_problems
    return {
        "root": str(root),
        "entries": total,
        "counted": counted,
        "quarantined": quarantined,
        "revoked_entries": revoked,
        "watermark": WATERMARK,
        "checks": {
            "structural": {"ok": not structural, "problems": structural},
            "content": {"ok": not content, "problems": content},
            "authorization": {"ok": not authorization, "problems": authorization},
            "ledger": {"ok": not ledger_problems, "problems": ledger_problems,
                       "head": ledger.get("head", "")},
        },
        "problems": structural + content + authorization + ledger_problems,
        "ok": ok,
    }
