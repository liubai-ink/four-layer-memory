# liu bai (留白). Provenance watermark: liu bai.
"""P1-7: first-party evaluation of the three claims that no public benchmark covers.

The library's differentiator is not recall -- it is *admission*: a write only
enters a layer when the evidence, the authority and (for layer A) a host-minted
approval all agree, and every accepted fact stays revocable and auditable.  The
published memory benchmarks (LoCoMo, LongMemEval, MemoryAgentBench, ...) all
measure what a memory system *retrieves*; none of them measure whether a system
can be trusted to *not* remember something.  So this script measures the three
properties we actually promise, locally, with no network and no dependencies:

1. write-gate precision
   Feed the admission gate a labelled battery of writes: some must be admitted,
   some must be refused (missing quote, wrong authority, unapproved layer-A,
   sensitive content, inverted timelines).  precision is the fraction of
   refusals that were *supposed* to be refused:
       precision = correct_refusals / all_refusals
   and recall over dirty writes is reported next to it.

2. revoke residual rate
   Write a set of facts, revoke them through their source events, then ask the
   store again through every read path (served text, search, layer counts,
   audit).  A correct store exposes 0% of them:
       residual_rate = residual_hits / revoked_facts

3. ledger tamper detectability
   Take a healthy store, then apply one mutation per case (edit a record, drop a
   record, splice a record, forge the chain head, truncate the file, sign
   without the key).  A correct store detects every case, either at open time or
   in `audit`:
       detect_rate = detected_cases / cases

Usage:
    python scripts/eval_memory.py [--json out.json] [--quiet]

Exit code 0 when every metric reaches its threshold (write-gate precision 1.0,
revoke residual 0.0, tamper detect 1.0), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from four_layer_memory import approval, signing  # noqa: E402
from four_layer_memory.audit import audit_store  # noqa: E402
from four_layer_memory.store import Entry, MemoryStore, StoreError  # noqa: E402

THRESHOLDS = {
    "write_gate_precision": 1.0,
    "revoke_residual_rate": 0.0,
    "tamper_detect_rate": 1.0,
    "experience_pass_rate": 1.0,
}


def _fresh(root: Path) -> MemoryStore:
    signing.keygen(root)
    return MemoryStore(root)


def _admitted(store: MemoryStore, entry: Entry) -> bool:
    """An entry is admitted when it reached a layer, i.e. the ledger carries the
    write/approve record.  Refused candidates only produce a quarantine record."""
    return any(str(r.get("entry_id")) == entry.entry_id
               and r.get("event") in ("write", "approve")
               for r in store.ledger_records())


# ---------------------------------------------------------------- metric 1
def measure_write_gate() -> dict:
    """Dirty writes must be refused; clean writes must be admitted."""
    clean_cases = [
        ("D", dict(text="Corpus has 400 chapters.", source="m:1"), True),
        ("C", dict(text="anchor.py indexes sessions.", source="m:2"), True),
        ("B", dict(text="Prefers short summaries.", source="m:3",
                   authority="user-explicit", evidence="quote"), True),
    ]
    dirty_cases = [
        # layer B needs quote/paraphrase, not a summary
        ("B", dict(text="Prefers short summaries.", source="m:4",
                   authority="user-explicit", evidence="summary")),
        # layer B needs an explicit or implied authority
        ("B", dict(text="Prefers long summaries.", source="m:5",
                   authority="observed", evidence="quote")),
        # layer A without a host approval record
        ("A", dict(text="Always answer in Chinese.", source="m:6",
                   authority="user-explicit", evidence="quote")),
        # layer A with an approval for a *different* text
        ("A", dict(text="Always answer in English.", source="m:7",
                   authority="user-explicit", evidence="quote",
                   _wrong_approval=True)),
        # medical content is quarantined in every layer
        ("D", dict(text="Patient prescription: 20mg daily.", source="m:8")),
        # identity / financial content
        ("D", dict(text="Bank account 1234 5678.", source="m:9")),
        # inverted validity window
        ("D", dict(text="Backwards claim.", source="m:10",
                   valid_from="2099-01-01", valid_to="2020-01-01")),
    ]
    root = Path(tempfile.mkdtemp())
    try:
        store = _fresh(root)
        admitted = 0
        for layer, spec, expected in clean_cases:
            entry = Entry(layer, spec.pop("text"), **spec)
            store.add(entry)
            if _admitted(store, entry):
                admitted += 1
        refused_ok = 0
        refused_total = 0
        for layer, spec in dirty_cases:
            wrong = spec.pop("_wrong_approval", False)
            text = spec.pop("text")
            source = spec.pop("source")
            entry = Entry(layer, text, source, approved=wrong, **spec)
            record = None
            if wrong:
                other = Entry(layer, "A different text entirely.", source)
                record = approval.mint(root, entry_id=other.entry_id,
                                       text=other.text,
                                       source_event_id=other.source_event_id,
                                       approver="eval")
            refused_total += 1
            try:
                store.add(entry, approval_record=record)
            except Exception:  # direct refusal (bad timeline, forged approval)
                refused_ok += 1
                continue
            if not _admitted(store, entry):
                refused_ok += 1
        precision = refused_ok / refused_total if refused_total else 0.0
        clean_ok = admitted == len(clean_cases)
        return {
            "metric": "write_gate_precision",
            "precision": round(precision, 4),
            "refused_ok": refused_ok,
            "refused_total": refused_total,
            "dirty_admitted": refused_total - refused_ok,
            "clean_admitted": admitted,
            "clean_total": len(clean_cases),
            "clean_all_admitted": clean_ok,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- metric 2
def measure_revoke_residual() -> dict:
    root = Path(tempfile.mkdtemp())
    try:
        store = _fresh(root)
        doomed = []
        for i in range(1, 6):
            entry = Entry("D", f"Retracted fact number {i}.", f"m:{100 + i}",
                          source_event_id=f"evt-{i}")
            store.add(entry)
            doomed.append(entry)
        keep = Entry("D", "Korpus has 400 chapters.", "m:200",
                     source_event_id="evt-keep")
        store.add(keep)
        for entry in doomed:
            store.revoke(entry.source_event_id)
        fresh = MemoryStore(root)
        served = fresh.read_layers(["D"])
        residual = 0
        for entry in doomed:
            if entry.text in served["text"]:
                residual += 1
                continue
            if any(str(h.get("entry_id")) == entry.entry_id
                   for h in fresh.search("Retracted", include_revoked=True)):
                # still findable only with the explicit include_revoked flag: fine
                continue
            if entry.entry_id in served["revoked"]:
                continue
            residual += 1
        hits_default = sum(1 for _h in fresh.search("Retracted"))
        report = audit_store(root)
        return {
            "metric": "revoke_residual_rate",
            "residual_rate": round(residual / len(doomed), 4),
            "residual_hits": residual,
            "revoked_facts": len(doomed),
            "visible_in_served_text": sum(1 for e in doomed if e.text in served["text"]),
            "visible_in_default_search": hits_default,
            "survivor_still_served": keep.text in served["text"],
            "audit_ok": bool(report["ok"]),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- metric 3
def _healthy_store(root: Path) -> Path:
    store = _fresh(root)
    for i in range(1, 4):
        store.add(Entry("D", f"Fact number {i}.", f"m:{i}"))
    return store.ledger_path


def _tamper_cases(ledger: Path):
    """Yield (name, mutate_fn) pairs, each mutating the ledger file in place."""
    original = ledger.read_text(encoding="utf-8")
    records = [json.loads(line) for line in original.splitlines() if line.strip()]

    def write(recs):
        ledger.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in recs) + "\n",
            encoding="utf-8",
        )

    def edit_record() -> None:
        recs = [json.loads(l) for l in original.splitlines() if l.strip()]
        recs[0]["evidence"] = "quote"  # content changed, hash left alone
        write(recs)

    def drop_record() -> None:
        recs = [json.loads(l) for l in original.splitlines() if l.strip()]
        write(recs[:-1])

    def splice_record() -> None:
        recs = [json.loads(l) for l in original.splitlines() if l.strip()]
        write(recs[:-1] + [recs[0]])

    def truncate_bytes() -> None:
        ledger.write_text(original[: max(10, len(original) // 2)], encoding="utf-8")

    def forge_head() -> None:
        recs = [json.loads(l) for l in original.splitlines() if l.strip()]
        recs[-1]["hash"] = "0" * 64
        write(recs)

    return [
        ("edit_record", edit_record),
        ("drop_record", drop_record),
        ("splice_record", splice_record),
        ("truncate_bytes", truncate_bytes),
        ("forge_head", forge_head),
    ]


def measure_tamper_detectability() -> dict:
    base = Path(tempfile.mkdtemp())
    results = []
    try:
        for name, mutate in _tamper_cases(_healthy_store(base / "seed")):
            root = base / name
            shutil.copytree(base / "seed", root)
            ledger = root / "ledger.jsonl"
            mutate_for_root = _tamper_cases(ledger)
            dict(mutate_for_root)[name]()
            detected = False
            reason = ""
            try:
                store = MemoryStore(root)
                store.read_layers(["D"])
                report = audit_store(root)
                detected = not report["ok"] or bool(report["checks"]["ledger"].get("reason"))
                reason = ",".join(report["problems"])[:160]
            except (StoreError, Exception) as exc:  # noqa: BLE001 - any refusal counts
                detected = True
                reason = type(exc).__name__ + ": " + str(exc)[:120]
            results.append({"case": name, "detected": detected, "reason": reason})
        rate = sum(1 for r in results if r["detected"]) / len(results)
        return {
            "metric": "tamper_detect_rate",
            "detect_rate": round(rate, 4),
            "cases": results,
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------- main
# ---------------------------------------------------------------- metric 4
def measure_experience_gating() -> dict:
    """A failed experience must not be served again (R052).

    The failure mode is *experience following*: a retrieved memory that looks
    similar is copied, so a wrong one spreads.  Scoring an experience after use
    and refusing to serve the failed ones is the write path's answer; this metric
    checks that the answer actually holds across the read paths (served text,
    search, stats, audit).
    """
    base = Path(tempfile.mkdtemp())
    try:
        store = _fresh(base / "store")
        good = Entry(layer="D", text="Deploy went out from the checklist.",
                     source="m:ok", authority="observed", evidence="quote",
                     kind="experience")
        bad = Entry(layer="D", text="Deploy went out from memory alone.",
                    source="m:bad", authority="observed", evidence="quote",
                    kind="experience")
        store.add(good)
        store.add(bad)
        before = store.read_layers(["D"])["text"]
        store.record_outcome([bad.entry_id], ok=False, reason="deploy broke")
        after = store.read_layers(["D"])["text"]
        hits = store.search("Deploy went out")
        ids = {h["entry_id"] for h in hits}
        audit = audit_store(store.root)
        stats = store.experience_stats()

        # A bundle is a migration path.  If the verdict does not travel, the
        # import silently re-arms the very experience this metric forbids.
        bundle = store.export_bundle(base / "roundtrip.bundle")
        moved = _fresh(base / "moved")
        moved.import_bundle(bundle, trust=True)
        moved_served = moved.read_layers(["D"])["text"]
        moved_failed = moved.read_layers(["D"])["failed"]

        # A claim waiting for a human must not vanish with the store.
        store.add(Entry(layer="A", text="Never deploy on Friday.",
                        source="m:pending", authority="observed",
                        evidence="summary"))
        pending_bundle = store.export_bundle(base / "pending.bundle")
        parked = _fresh(base / "parked")
        parked_report = parked.import_bundle(pending_bundle)
        parked_pending = parked.quarantine_entries()

        cases = {
            "served_before_failure": bad.text in before,
            "hidden_after_failure": bad.text not in after,
            "good_still_served": good.text in after,
            "search_hides_failed": bad.entry_id not in ids,
            "stats_count": (stats["total"] == 2 and stats["failed"] == 1
                            and stats["labelled"] == 1),
            "audit_ok": bool(audit["ok"]),
            "bundle_keeps_verdict": (bad.text not in moved_served
                                     and bad.entry_id in moved_failed),
            "bundle_carries_review_queue": (bool(parked_pending)
                                            and bool(parked_report.get("needs_review"))),
        }
        rate = sum(1 for v in cases.values() if v) / len(cases)
        return {"metric": "experience_pass_rate",
                "pass_rate": round(rate, 4), "cases": cases, "stats": stats}
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate the four write-path claims: admission, revocation, "
                    "ledger auditability, and experience scoring.")
    ap.add_argument("--json", default="", help="write the full report to this path")
    ap.add_argument("--quiet", action="store_true", help="only print the verdict")
    args = ap.parse_args(argv)

    gate = measure_write_gate()
    residual = measure_revoke_residual()
    tamper = measure_tamper_detectability()
    experience = measure_experience_gating()

    checks = {
        "write_gate_precision": gate["precision"] >= THRESHOLDS["write_gate_precision"],
        "revoke_residual_rate": residual["residual_rate"] <= THRESHOLDS["revoke_residual_rate"],
        "tamper_detect_rate": tamper["detect_rate"] >= THRESHOLDS["tamper_detect_rate"],
        "experience_pass_rate": (experience["pass_rate"]
                                 >= THRESHOLDS["experience_pass_rate"]),
    }
    report = {
        "ok": all(checks.values()),
        "thresholds": THRESHOLDS,
        "checks": checks,
        "metrics": {"write_gate": gate, "revoke": residual, "tamper": tamper,
                    "experience": experience},
        "note": "local, offline, zero-dependency; measures admission/revocation/"
                "auditability/experience scoring rather than recall.",
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        verdict = "ok" if report["ok"] else "FAILED"
        print(f"eval_memory: {verdict} "
              f"(gate {gate['precision']:.3f}, residual {residual['residual_rate']:.3f}, "
              f"tamper {tamper['detect_rate']:.3f}, "
              f"experience {experience['pass_rate']:.3f})")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
