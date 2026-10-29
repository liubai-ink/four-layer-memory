# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Command line interface: init / add / approve / consolidate / status / audit / demo.

The CLI is the *host channel*: approval of a quarantined claim lives here and is
never exposed to the MCP write path, so an agent cannot approve its own claim
(v0.7 F01).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import approval, signing
from .audit import audit_store
from .distill import Candidate, plan
from .handoff import Handoff
from .layers import Layer
from .store import DEFAULT_COOLDOWN_ENTRIES, Entry, MemoryStore, StoreError
from .triggers import decide
from .watermark import WATERMARK


def _approvable(store: MemoryStore, entry_id: str) -> dict:
    pending = [e for e in store.quarantine_entries() if e.get("entry_id") == entry_id]
    if not pending:
        raise StoreError(f"no quarantined entry {entry_id}")
    return pending[0]


def _approve(store: MemoryStore, entry_id: str, approver: str) -> dict:
    found = _approvable(store, entry_id)
    record = approval.mint(
        store.root, entry_id=entry_id, text=str(found.get("text") or ""),
        source_event_id=str(found.get("source_event_id") or ""), approver=approver,
    )
    return store.approve(entry_id, record, approver)


def cmd_init(args) -> int:
    store = MemoryStore(args.root, session_id=getattr(args, "session", "unnamed"))
    info = signing.keygen(args.root)
    approval.ensure_key(args.root)
    status = store.status()
    status["signing"] = {"alg": signing.algorithm(args.root), "created": info["created"]}
    status["approval_fingerprint"] = approval.fingerprint(args.root)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_keygen(args) -> int:
    info = signing.keygen(args.root, alg=args.alg, force=args.force)
    info["fingerprint"] = signing.public_key_hex(args.root)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_add(args) -> int:
    store = MemoryStore(args.root)
    e = store.add(
        Entry(
            layer=args.layer,
            text=args.text,
            source=args.source,
            authority=args.authority,
            evidence=args.evidence,
            lifecycle=args.lifecycle,
            source_event_id=args.source_event,
            scope=getattr(args, "scope", "") or "",
            expires_at=getattr(args, "expires_at", "") or "",
            approved=False,  # a write never grants approval by itself
        )
    )
    pending = [x for x in store.quarantine_entries() if x.get("entry_id") == e.entry_id]
    accepted = not pending
    if pending and args.approved:
        # --approved is the host exercising its own approval channel; the record
        # is minted and verified exactly like an interactive approval.
        _approve(store, e.entry_id, "cli")
        # The claim was filed in quarantine on its way in (the write side never
        # holds approval material), so close that trail now that the host channel
        # accepted it -- otherwise every accepted add leaves a phantom "waiting
        # for the host channel" record behind.
        store.resolve_quarantine(e.entry_id, decision="accepted", approver="cli")
        accepted = True
        pending = []
    print(json.dumps({"added": e.text, "layer": e.layer, "quarantined": bool(pending),
                      "accepted": accepted, "entry_id": e.entry_id,
                      "watermark": WATERMARK}, ensure_ascii=False))
    return 0


def cmd_approve(args) -> int:
    store = MemoryStore(args.root)
    result = _approve(store, args.entry_id, args.approver)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_consolidate(args) -> int:
    store = MemoryStore(args.root)
    print(json.dumps(store.consolidate(args.anchor, args.usage, args.cooldown),
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    store = MemoryStore(args.root)
    print(json.dumps(store.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_triggers(args) -> int:
    print(decide(args.usage, args.turns, args.entries, args.cooldown).value)
    return 0


def cmd_plan(args) -> int:
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    cands = [Candidate(text=c["text"], source=c["source"], tags=tuple(c.get("tags", []))) for c in raw]
    planned = plan(cands)
    print(json.dumps({k: [e.text for e in v] for k, v in planned.items()}, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff(args) -> int:
    h = Handoff(args.state)
    if args.mark:
        h.mark_consolidated(args.mark)
        print(json.dumps(h.state.to_json()))
        return 0
    records = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.report:
        print(json.dumps(h.resume_report(records), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(h.resume(records), ensure_ascii=False, indent=2))
    return 0


def cmd_quarantine(args) -> int:
    store = MemoryStore(args.root)
    if args.reject:
        result = store.review(args.reject, accept=False)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.list:
        print(json.dumps(store.quarantine_entries(), ensure_ascii=False, indent=2))
        return 0
    print(store.quarantine_path.read_text(encoding="utf-8"))
    return 0


def cmd_calibrate(args) -> int:
    from .calibrate import calibrate, load_samples, write_thresholds

    samples = load_samples(args.file)
    report = calibrate(samples, host_fold=args.fold, host_emergency=args.emergency)
    if args.out:
        write_thresholds(args.out, report["watermark"])
        report["written_to"] = str(args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke(args) -> int:
    store = MemoryStore(args.root)
    print(json.dumps(store.revoke(args.source_event), ensure_ascii=False, indent=2))
    return 0


def cmd_archive_search(args) -> int:
    store = _store(args)
    hits = store.archive.search(args.query, limit=args.limit,
                                include_revoked=not args.exclude_revoked)
    print(json.dumps({"query": args.query, "count": len(hits), "entries": hits},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_archive_verify(args) -> int:
    store = _store(args)
    report = store.archive.verify()
    print(json.dumps({"ok": report["ok"], "events": report["count"],
                      "problems": report["problems"],
                      "stats": store.archive.stats()},
                     ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_audit(args) -> int:
    report = audit_store(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_compact(args) -> int:
    store = MemoryStore(args.root)
    layers = [x.strip().upper() for x in (args.layer or []) if x.strip()] or None
    report = store.compact(apply=args.apply, layers=layers)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.audit_after and args.apply:
        after = audit_store(args.root)
        return 0 if after["ok"] else 1
    return 0


def cmd_export(args) -> int:
    store = MemoryStore(args.root)
    out = store.export_bundle(args.out)
    print(str(out))
    return 0


def cmd_demo(args) -> int:
    """End-to-end smoke test in a throw-away directory."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        store = MemoryStore(td, session_id="demo")
        signing.keygen(td)
        approval.ensure_key(td)
        store.add(Entry("A", "Always keep provenance on every memory entry.", "demo:m1",
                        authority="user-explicit", evidence="quote",
                        source_event_id="demo:evt"))
        _approve(store, store.quarantine_entries()[0]["entry_id"], "demo")
        store.add(Entry("B", "Prefer tables over paragraphs in comparisons.", "demo:m2"))
        store.add(Entry("C", "The corpus is queried with corpus_query.py.", "demo:m3"))
        store.add(Entry("D", "The project started on 2026-10-19.", "demo:m4"))
        store.consolidate("m00042", 0.41)
        bundle = store.export_bundle(Path(td) / "bundle.json")
        report = audit_store(td)
        print(json.dumps({"status": store.status(), "audit": report,
                          "bundle_bytes": bundle.stat().st_size}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="four-layer-memory")
    p.add_argument("--root", default=".memory")
    sub = p.add_subparsers(dest="cmd", required=True)

    ini = sub.add_parser("init")
    ini.add_argument("--session", default="unnamed")

    a = sub.add_parser("add")
    a.add_argument("layer", choices=[l.value for l in Layer])
    a.add_argument("text")
    a.add_argument("--source", required=True)
    a.add_argument("--authority", default="inferred",
                   choices=["user-explicit", "user-implied", "observed", "inferred",
                            "model-generated", "none"])
    a.add_argument("--evidence", default="summary",
                   choices=["quote", "paraphrase", "summary", "inference", "none"])
    a.add_argument("--lifecycle", default="standing",
                   choices=["standing", "project-scoped", "expiring"])
    a.add_argument("--source-event", default="")
    a.add_argument("--scope", default="")
    a.add_argument("--expires-at", default="")
    a.add_argument("--approved", action="store_true",
                   help="host approval of this write in the same call (layer A)")

    ap = sub.add_parser("approve")
    ap.add_argument("entry_id")
    ap.add_argument("--approver", default="host")

    c = sub.add_parser("consolidate")
    c.add_argument("--anchor", required=True)
    c.add_argument("--usage", type=float, required=True)
    c.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_ENTRIES)

    sub.add_parser("status")

    t = sub.add_parser("triggers")
    t.add_argument("--usage", type=float, required=True)
    t.add_argument("--turns", type=int, default=0)
    t.add_argument("--entries", type=int, default=0)
    t.add_argument("--cooldown", type=int, default=0)

    pl = sub.add_parser("plan")
    pl.add_argument("--file", required=True)

    h = sub.add_parser("handoff")
    h.add_argument("--state", default=".memory/handoff.json")
    h.add_argument("--mark", default="")
    h.add_argument("--file", default="")
    h.add_argument("--report", action="store_true")

    q = sub.add_parser("quarantine")
    q.add_argument("--reject", default="")
    q.add_argument("--list", action="store_true")

    kg = sub.add_parser("keygen")
    kg.add_argument("--alg", choices=["hmac-sha256", "ed25519"], default="hmac-sha256")
    kg.add_argument("--force", action="store_true")

    cal = sub.add_parser("calibrate")
    cal.add_argument("--file", required=True)
    cal.add_argument("--out", default="")
    cal.add_argument("--fold", type=float, default=0.45)
    cal.add_argument("--emergency", type=float, default=0.95)
    asr = sub.add_parser("archive-search")
    asr.add_argument("query")
    asr.add_argument("--limit", type=int, default=10)
    asr.add_argument("--exclude-revoked", action="store_true")
    asr.set_defaults(func=cmd_archive_search)
    sub.add_parser("archive-verify").set_defaults(func=cmd_archive_verify)
    rv = sub.add_parser("revoke")
    rv.add_argument("source_event")

    sub.add_parser("audit")

    cp = sub.add_parser("compact")
    cp.add_argument("--apply", action="store_true",
                    help="rewrite the layer files (default: dry-run)")
    cp.add_argument("--layer", action="append", default=[],
                    help="limit to layer A/B/C/D (repeatable)")
    cp.add_argument("--audit-after", action="store_true",
                    help="run the audit after applying and fail if it is not ok")

    e = sub.add_parser("export")
    e.add_argument("--out", required=True)

    sub.add_parser("demo")

    args = p.parse_args(argv)
    fn = {
        "init": cmd_init, "add": cmd_add, "approve": cmd_approve,
        "consolidate": cmd_consolidate, "status": cmd_status,
        "triggers": cmd_triggers, "plan": cmd_plan, "handoff": cmd_handoff,
        "audit": cmd_audit, "export": cmd_export, "keygen": cmd_keygen,
        "quarantine": cmd_quarantine, "calibrate": cmd_calibrate,
        "revoke": cmd_revoke, "demo": cmd_demo, "compact": cmd_compact,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
