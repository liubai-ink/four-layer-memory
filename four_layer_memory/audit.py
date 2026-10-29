# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Audit a memory root: structure, content, authorization, ledger.

v0.7.1 makes every check *structured*: findings carry a machine readable
``code`` so a caller can tell "no finding" from "a finding that was not
recognised".  Concretely:

* the loader and the audit agree on what is a record (F06);
* a layer-A entry is only accepted when a host-minted approval record is in
  the ledger and verifies against the entry it approves (F04);
* a secret-like token reported by the content scan fails the check
  (F07 - the old code compared against a stale message suffix);
* a truncated ledger fails the audit instead of being overwritten by a
  later recovery (F01).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import admission, approval, signing
from .layers import Layer, spec
from .store import Entry, MemoryStore, _norm, _ts
from .watermark import WATERMARK

REQUIRED = ("state.json", "ledger.jsonl", "quarantine/pending.md")
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
USERPATH = re.compile("[A-Za-z]:" + chr(92) + chr(92) + "Users" + chr(92) + chr(92) + "|/home/[^/]+/")
SENSITIVE = admission.SENSITIVE_PATTERNS


def _scan(text: str, label: str) -> list[dict]:
    findings: list[dict] = []
    if SECRET.search(text):
        findings.append({"code": "secret_like_token", "label": label,
                         "message": "secret-like token (sk-...)"})
    if USERPATH.search(text):
        findings.append({"code": "absolute_user_path", "label": label,
                         "message": "absolute user path"})
    return findings


def audit_store(root: str | Path) -> dict:
    root = Path(root)
    checks: dict[str, dict] = {}
    problems: list[str] = []

    # -- structural --------------------------------------------------------
    structural: dict = {"ok": True, "findings": []}
    missing = [name for name in REQUIRED if not (root / name).exists()]
    if missing:
        structural["missing"] = missing
        structural["findings"].append({"code": "missing_files", "files": missing})
    state: dict = {}
    try:
        store = MemoryStore(root, recover=False)
        state = store.state.__dict__ if store.state else {}
    except Exception as exc:  # pragma: no cover - defensive
        structural["open_error"] = repr(exc)
        problems.append(f"store cannot be opened: {exc!r}")
        structural["ok"] = False
        checks["structural"] = structural
        for name in ("content", "authorization", "ledger"):
            checks[name] = {"ok": False, "skipped": True}
        return _report(checks, problems)

    layer_text = {layer.value: (store.path_for(layer).read_text(encoding="utf-8")
                                if store.path_for(layer).exists() else "") for layer in Layer}

    unknown: dict[str, int] = {}
    unknown_lines: list[str] = []
    for layer in Layer:
        _blocks, unk = store._parse_layer(layer)
        if unk:
            unknown[spec(layer).target] = len(unk)
            unknown_lines.extend(f"{spec(layer).target}: {line[:80]}" for line in unk[:5])
    if unknown:
        structural["unknown"] = unknown
        structural["unknown_sample"] = unknown_lines[:20]
        structural["findings"].append({"code": "unknown_layer_lines", "counts": unknown})

    trunc = dict(state.get("truncated") or {})
    if trunc:
        structural["ledger_truncated"] = trunc
        structural["findings"].append({"code": "ledger_truncated", "detail": trunc})

    if state.get("watermark") != WATERMARK:
        structural["findings"].append({"code": "missing_watermark"})

    counts = store.state.layer_counts or {}
    for layer in Layer:
        name = spec(layer).name
        actual = layer_text[layer.value].count("\n- ") + (1 if layer_text[layer.value].startswith("- ") else 0)
        if int(counts.get(name, 0)) != actual:
            structural["findings"].append({"code": "count_mismatch", "layer": name,
                                           "state": counts.get(name), "file": actual})

    structural["ok"] = not structural["findings"]
    problems.extend(f"structural: {f.get('code')} {f.get('detail') or f.get('counts') or ''}".strip()
                    for f in structural["findings"])
    checks["structural"] = structural

    # -- temporal (dual timeline) ------------------------------------------
    temporal: dict = {"ok": True, "findings": [], "historical": 0, "future": 0}
    for layer in Layer:
        for entry, _raw in store._parse_layer(layer)[0]:
            start = _ts(entry.valid_from) if entry.valid_from else None
            end = _ts(entry.valid_to) if entry.valid_to else None
            if start is not None and end is not None and start > end:
                temporal["findings"].append({"code": "valid_window_inverted",
                                             "entry_id": entry.entry_id,
                                             "layer": spec(layer).target})
            if store._is_historical(entry):
                temporal["historical"] += 1
            elif store._is_future(entry):
                temporal["future"] += 1
    temporal["ok"] = not temporal["findings"]
    if temporal["findings"]:
        problems.extend(f"temporal: {f['code']} {f.get('entry_id')}"
                        for f in temporal["findings"])
    checks["temporal"] = temporal

    # -- supersede (write-side merge) ---------------------------------------
    supersede: dict = {"ok": True, "findings": [], "chains": 0}
    _records = store.ledger_records()
    _committed = {str(r.get("entry_id")) for r in _records
                  if r.get("event") in ("write", "approve")}
    for _entry in store._servable(_records):
        if not _entry.supersedes:
            continue
        supersede["chains"] += 1
        if _entry.supersedes not in _committed:
            supersede["findings"].append({"code": "supersedes_unknown_target",
                                          "entry_id": _entry.entry_id,
                                          "target": _entry.supersedes})
    supersede["ok"] = not supersede["findings"]
    if supersede["findings"]:
        problems.extend(f"supersede: {f['code']} {f.get('entry_id')}"
                        for f in supersede["findings"])
    checks["supersede"] = supersede

    # -- content -----------------------------------------------------------
    content: dict = {"ok": True, "findings": []}
    findings: list[dict] = []
    for layer in Layer:
        findings.extend(_scan(layer_text[layer.value], spec(layer).target))
    pend = store.quarantine_path.read_text(encoding="utf-8") if store.quarantine_path.exists() else ""
    findings.extend(_scan(pend, "quarantine/pending.md"))
    content["findings"] = findings
    if findings:
        content["codes"] = sorted({f["code"] for f in findings})
        problems.extend(f"{f['label']}: {f['message']}" for f in findings)
    content["ok"] = not findings
    checks["content"] = content

    # -- authorization -----------------------------------------------------
    authorization: dict = {"ok": True, "findings": []}
    auth: list[dict] = []
    records = store.ledger_records()
    expected = store.expected_layers()
    for layer in Layer:
        if _norm(expected[layer.value]) != _norm(layer_text[layer.value]):
            auth.append({"code": "layer_diverges", "layer": spec(layer).target})
    bound_tx = {str(r.get("tx")) for r in records if r.get("event") == "bind" and r.get("tx")}
    known_ids = {str(r.get("entry_id")) for r in records
                 if r.get("event") in ("write", "approve")}
    for layer in Layer:
        for entry, _raw in store._parse_layer(layer)[0]:
            if entry.entry_id and entry.entry_id not in known_ids:
                auth.append({"code": "orphan_entry", "entry_id": entry.entry_id,
                             "layer": spec(layer).target})
    root_key = store.root
    # A revoked entry stays in the ledger for replay, but it is no longer served:
    # its approval record is not re-verified, otherwise revoking a legacy entry
    # could never clear the authorization check.
    revoked_ids, revoked_sources = store.revoked_sets()
    revoked_skipped = 0
    for rec in records:
        if rec.get("event") not in ("write", "approve"):
            continue
        eid = str(rec.get("entry_id") or "")
        if eid and (eid in revoked_ids
                    or str(rec.get("source_event_id") or "") in revoked_sources):
            revoked_skipped += 1
            continue
        if rec.get("tx") and str(rec["tx"]) not in bound_tx:
            auth.append({"code": "unbound_transaction", "tx": rec.get("tx")})
        try:
            entry = Entry.from_record(rec)
        except Exception:  # pragma: no cover - defensive
            auth.append({"code": "unreadable_record"})
            continue
        if entry.layer != "A":
            continue
        decision = admission.decide(
            entry.layer, entry.authority, entry.evidence,
            source_event_id=entry.source_event_id,
            approved=entry.approved,
            text=entry.text,
        )
        if not decision.accept:
            auth.append({"code": "a_not_authorized", "entry_id": entry.entry_id,
                         "reason": decision.reason})
        if rec.get("event") == "write":
            auth.append({"code": "a_without_approval", "entry_id": entry.entry_id})
            continue
        arec = rec.get("approval_record")
        if not isinstance(arec, dict) or not approval.verify(root_key, arec):
            auth.append({"code": "approval_unverified", "entry_id": entry.entry_id})
            continue
        text_sha = hashlib.sha256(entry.text.encode("utf-8")).hexdigest()
        if arec.get("text_sha256") != text_sha:
            auth.append({"code": "approval_text_mismatch", "entry_id": entry.entry_id})
        if str(arec.get("source_event_id") or "") != str(entry.source_event_id or ""):
            auth.append({"code": "approval_source_mismatch", "entry_id": entry.entry_id})
        if not str(arec.get("approver") or "").strip():
            auth.append({"code": "approval_missing_approver", "entry_id": entry.entry_id})
    authorization["revoked_skipped"] = revoked_skipped
    authorization["findings"] = auth
    if auth:
        authorization["codes"] = sorted({f["code"] for f in auth})
        problems.extend(f"authorization: {f['code']} {f.get('entry_id') or f.get('layer') or ''}".strip()
                        for f in auth)
    authorization["ok"] = not auth
    checks["authorization"] = authorization

    # -- archive -----------------------------------------------------------
    archive_check: dict = {"ok": True, "findings": []}
    try:
        chain = store.archive.verify()
        archive_check["events"] = chain.get("count", 0)
        archive_check["last_sha256"] = store.archive.last_sha256
        if not chain.get("ok"):
            archive_check["findings"].append(
                {"code": "archive_chain_broken",
                 "problems": list(chain.get("problems") or [])[:10]})
        served = {str(r.get("entry_id")) for r in records
                  if r.get("event") in ("write", "approve")}
        captured = store.archive.entry_ids()
        missing = sorted(x for x in served if x and x not in captured)
        if missing:
            archive_check["findings"].append(
                {"code": "archive_missing_capture", "count": len(missing),
                 "entry_ids": missing[:10]})
    except Exception as exc:  # pragma: no cover - defensive
        archive_check["findings"].append(
            {"code": "archive_error", "message": repr(exc)})
    if archive_check["findings"]:
        archive_check["codes"] = sorted({f["code"] for f in archive_check["findings"]})
        problems.extend(f"archive: {f['code']}" for f in archive_check["findings"])
    archive_check["ok"] = not archive_check["findings"]
    checks["archive"] = archive_check

    # -- ledger ------------------------------------------------------------
    integrity = store.ledger_integrity()
    ledger_check = {"ok": bool(integrity.get("ok")), "records": integrity.get("records"),
                    "signed": integrity.get("signed"), "verified": integrity.get("verified"),
                    "verified_with": integrity.get("verified_with")}
    if trunc:
        ledger_check["ok"] = False
        ledger_check["reason"] = "ledger truncated"
        problems.append("ledger: truncated")
    if not integrity.get("ok"):
        ledger_check["reason"] = integrity.get("reason")
        problems.append(f"ledger: {integrity.get('reason')}")
    checks["ledger"] = ledger_check

    return _report(checks, problems)


def _report(checks: dict, problems: list[str]) -> dict:
    ok = all(bool(c.get("ok")) for c in checks.values())
    return {
        "ok": ok,
        "checks": checks,
        "problems": problems,
        "store_version": MemoryStore.VERSION if hasattr(MemoryStore, "VERSION") else "0.7.2",
        "watermark": WATERMARK,
    }
