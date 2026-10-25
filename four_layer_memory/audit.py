# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Invariants an on-disk memory store should satisfy.

Audit reports four checks separately, because they make different claims:
  structural   -- files exist, provenance is present, counters agree
  content      -- no credential-shaped strings or absolute user paths
  authorization-- no layer-A entry lacks explicit quoted approval, and every
                  layer file still hashes to what the ledger says it should
  ledger       -- the hash chain recomputes
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import admission
from .layers import Layer, spec
from .store import MemoryStore
from .watermark import WATERMARK

_BS = chr(92)
FORBIDDEN_PATH = re.compile("[A-Za-z]:" + _BS + _BS + "Users" + _BS + _BS + "|/home/[^/]+/")
SECRET = re.compile("sk-" + "[A-Za-z0-9_-]{16,}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    # Authorization: replay the ledger; A writes must have been approved and sourced.
    bad_a = 0
    revoked = 0
    expect_file: dict[str, str] = {}
    for rec in store.ledger_records():
        event = rec.get("event")
        if event == "revoke":
            revoked += len(rec.get("entry_ids") or [])
            continue
        if event == "bind":
            if rec.get("layer_sha256"):
                expect_file[str(rec.get("layer", ""))] = str(rec["layer_sha256"])
            continue
        if event not in ("write", "approve"):
            continue
        layer = str(rec.get("layer", ""))
        if event == "write" or layer == "A":
            decision = admission.decide(
                layer or "D", rec.get("authority"), rec.get("evidence"),
                source_event_id=str(rec.get("source_event_id") or ""),
                approved=bool(rec.get("approved")),
                text=str(rec.get("text") or ""),
            )
            if not decision.accept and layer == "A":
                bad_a += 1
        if rec.get("layer_sha256"):
            expect_file[layer] = str(rec["layer_sha256"])
    if bad_a:
        authorization.append(f"{bad_a} layer-A entries lack explicit quoted approval")

    # Every layer file must still hash to the value recorded when it was last
    # written through the store; otherwise the bytes changed outside the ledger.
    for layer in Layer:
        p = store.path_for(layer)
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        has_entries = any(line.startswith("- ") for line in text.splitlines())
        if not has_entries:
            continue
        if layer.value not in expect_file:
            authorization.append(
                f"layer file has content but no ledger record covers it: {spec(layer).target}"
            )
    for layer, expected in expect_file.items():
        p = store.path_for(layer)
        if not p.exists():
            authorization.append(f"layer file vanished: {spec(layer).target}")
            continue
        if _sha(p.read_text(encoding="utf-8")) != expected:
            authorization.append(
                f"layer file does not match the ledger hash: {spec(layer).target} "
                f"(edited outside the store?)"
            )

    ledger = store.ledger_integrity()
    if not ledger["ok"]:
        ledger_problems.append(
            "ledger chain broken at record " + str(ledger.get("at")) + ": " + str(ledger.get("reason"))
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
