# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Command line interface: init / add / consolidate / status / handoff / audit / demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import audit_store
from .distill import Candidate, plan
from .handoff import Handoff
from .layers import Layer
from . import signing
from .store import Entry, MemoryStore
from .triggers import decide
from .watermark import WATERMARK


def cmd_init(args) -> int:
    store = MemoryStore(args.root, session_id=getattr(args, "session", "unnamed"))
    info = signing.keygen(args.root)
    status = store.status()
    status["signing"] = {"alg": signing.algorithm(args.root), "created": info["created"]}
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
            approved=args.approved,
        )
    )
    pending = [x for x in store.quarantine_entries() if x.get("text") == e.text]
    print(json.dumps({"added": e.text, "layer": e.layer, "quarantined": bool(pending),
                      "entry_id": e.entry_id, "watermark": WATERMARK}, ensure_ascii=False))
    return 0


def cmd_consolidate(args) -> int:
    store = MemoryStore(args.root)
    print(json.dumps(store.consolidate(args.anchor, args.usage, args.cooldown), ensure_ascii=False, indent=2))
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
    print(json.dumps(h.resume(records), ensure_ascii=False, indent=2))
    return 0


def cmd_quarantine(args) -> int:
    store = MemoryStore(args.root)
    if args.accept or args.reject:
        result = store.review(args.accept or args.reject, accept=bool(args.accept),
                              token=args.token)
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


def cmd_audit(args) -> int:
    report = audit_store(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


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
        store.add(Entry("A", "Always keep provenance on every memory entry.", "demo:m1",
                       authority="user-explicit", evidence="quote"))
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
                   choices=["user-explicit", "user-implied", "observed", "inferred", "model-generated"])
    a.add_argument("--evidence", default="summary",
                   choices=["quote", "paraphrase", "summary", "inference"])
    a.add_argument("--lifecycle", default="standing",
                   choices=["standing", "project-scoped", "expiring"])
    a.add_argument("--source-event", default="")
    a.add_argument("--approved", action="store_true",
                   help="record that a human approved this write (required for layer A)")

    c = sub.add_parser("consolidate")
    c.add_argument("--anchor", required=True)
    c.add_argument("--usage", type=float, required=True)
    c.add_argument("--cooldown", type=int, default=50)

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

    q = sub.add_parser("quarantine")
    q.add_argument("--accept", default="")
    q.add_argument("--reject", default="")
    q.add_argument("--list", action="store_true")
    q.add_argument("--token", default="",
                   help="approval token shown when the entry was quarantined")

    kg = sub.add_parser("keygen")
    kg.add_argument("--alg", choices=["hmac-sha256", "ed25519"], default="hmac-sha256")
    kg.add_argument("--force", action="store_true")

    cal = sub.add_parser("calibrate")
    cal.add_argument("--file", required=True)
    cal.add_argument("--out", default="")
    cal.add_argument("--fold", type=float, default=0.45)
    cal.add_argument("--emergency", type=float, default=0.95)
    rv = sub.add_parser("revoke")
    rv.add_argument("source_event")

    sub.add_parser("audit")

    e = sub.add_parser("export")
    e.add_argument("--out", required=True)

    sub.add_parser("demo")

    args = p.parse_args(argv)
    fn = {
        "init": cmd_init, "add": cmd_add, "consolidate": cmd_consolidate,
        "status": cmd_status, "triggers": cmd_triggers, "plan": cmd_plan,
        "handoff": cmd_handoff, "audit": cmd_audit, "export": cmd_export,
        "keygen": cmd_keygen, "quarantine": cmd_quarantine,
        "calibrate": cmd_calibrate, "revoke": cmd_revoke,
        "demo": cmd_demo,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
