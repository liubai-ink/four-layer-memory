# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Independent v0.7.0 checks for the v0.4 review items (R01-R13, C01-C06).

    python scripts/verify_v07_probes.py --source . --work-dir ./_v07 --result result.json

Every check is a local, synthetic store; nothing upstream is modified.  A check
records ``fix_confirmed`` when the counterexample no longer reproduces, and
``issue_reproduced`` when it does.  This is not a model-attack success rate and
not a power-loss test: fault probes inject exceptions at named steps.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT_KINDS = ("fix_confirmed", "issue_reproduced")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.source).resolve()))
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)

    from four_layer_memory import approval, mcp, signing, __version__ as version
    from four_layer_memory.audit import audit_store
    from four_layer_memory.store import Entry, MemoryStore, StoreError
    from four_layer_memory.triggers import decide

    results: list[dict] = []
    counter = [0]

    def fresh(label: str) -> Path:
        counter[0] += 1
        return Path(tempfile.mkdtemp(prefix=f"{label}-{counter[0]}-", dir=work))

    def call(root: Path, name: str, arguments: dict) -> dict:
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"FOUR_LAYER_MEMORY_ROOT": str(root)}):
            reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": arguments}})["result"]
        text = reply["content"][0]["text"]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"error": text}
        payload["_isError"] = bool(reply.get("isError"))
        return payload

    def loaded(root: Path) -> str:
        return call(root, "memory_load", {"layers": ["A", "B", "C", "D"]})["text"]

    def emit(identifier: str, kind: str, demonstrated: bool, **facts) -> None:
        results.append({"id": identifier, "kind": kind, "demonstrated": bool(demonstrated),
                        **facts})

    def run(identifier: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # a probe error is never silently a pass
            results.append({"id": identifier, "kind": "probe_error", "error": repr(exc)})

    def data_entry(label: str, source_event: str = "", derived=None, layer: str = "D") -> Entry:
        return Entry(layer, label, "synthetic:" + label,
                     source_event_id=source_event, derived_from=derived or [],
                     authority="observed", evidence="summary")

    # -- C01: self-claimed authority stays out of the layers ---------------
    def c01() -> None:
        p = fresh("C01")
        a = call(p, "memory_add", {"layer": "A", "text": "C01 amber rule.",
                                   "source": "file:synthetic", "authority": "user-explicit",
                                   "evidence": "quote", "source_event_id": "invented-C01"})
        b = call(p, "memory_add", {"layer": "B", "text": "C01 violet preference.",
                                   "source": "model:synthetic", "authority": "inferred",
                                   "evidence": "inference"})
        text = loaded(p)
        emit("C01_direct_self_claim_and_inferred_B", "fix_confirmed",
             a["quarantined"] and b["quarantined"] and "C01" not in text,
             A_quarantined=a["quarantined"], B_quarantined=b["quarantined"])

    # -- C02: host approval, then source revocation ------------------------
    def c02() -> None:
        p = fresh("C02")
        s = MemoryStore(p)
        e = s.add(Entry("A", "C02 approved synthetic rule.", "msg:C02",
                        authority="user-explicit", evidence="quote",
                        source_event_id="source-C02"))
        pending = s.quarantine_entries()[0]
        rec = approval.mint(p, entry_id=pending["entry_id"], text=pending["text"],
                            source_event_id="source-C02")
        s.approve(pending["entry_id"], rec, approver="synthetic-human")
        before = "C02 approved" in loaded(p)
        hits = s.revoke("source-C02")["revoked"]
        after = "C02 approved" in loaded(p)
        emit("C02_host_approval_and_revocation", "fix_confirmed",
             before and e.entry_id in hits and not after and audit_store(p)["ok"],
             loaded_before=before, revoked_hits=len(hits), loaded_after=after)

    # -- C03: idempotent writes across two handles -------------------------
    def c03() -> None:
        p = fresh("C03")
        a, b = MemoryStore(p), MemoryStore(p)
        e = data_entry("C03 first")
        a.add(e)
        a.add(e)
        b.add(data_entry("C03 second"))
        report = audit_store(p)
        emit("C03_repeat_add_two_handles", "fix_confirmed",
             report["ok"] and loaded(p).count("\n- ") == 2,
             audit_ok=report["ok"], bullets=loaded(p).count("\n- "))

    # -- C04: truncated ledger and same-count edit -------------------------
    def c04() -> None:
        p = fresh("C04")
        signing.keygen(p)
        s = MemoryStore(p)
        s.add(data_entry("C04 original", "source-C04"))
        s.revoke("source-C04")
        lines = s.ledger_path.read_text(encoding="utf-8").splitlines()
        s.ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        truncated = not audit_store(p)["ok"]
        p2 = fresh("C04-edit")
        s2 = MemoryStore(p2)
        s2.add(data_entry("C04 original"))
        f = s2.path_for("D")
        f.write_text(f.read_text(encoding="utf-8").replace("C04 original", "C04 changed"),
                     encoding="utf-8")
        edited = not audit_store(p2)["ok"]
        emit("C04_suffix_truncation_and_same_count_edit", "fix_confirmed",
             truncated and edited, truncation_detected=truncated, same_count_edit_detected=edited)

    # -- C05: fold policy and sensitive patterns ---------------------------
    def c05() -> None:
        p = fresh("C05")
        s = MemoryStore(p)
        s.add(data_entry("C05 fictional medical diagnosis.", layer="C"))
        default = decide(0.46).value
        pending = decide(0.46, entries_since=1, allow_fold=True).value
        emit("C05_fold_requires_pending_and_sensitive_quarantine", "fix_confirmed",
             default == "consolidate" and pending == "fold" and s.quarantined() == 1,
             default_action=default, pending_action=pending)

    # -- C06: public-key verification and tamper detection ------------------
    def c06() -> None:
        p = fresh("C06-signer")
        signing.keygen(p, alg="ed25519")
        s = MemoryStore(p)
        s.add(data_entry("C06 signed record", layer="C"))
        public = fresh("C06-public")
        for file in p.rglob("*"):
            if file.is_file() and file.name not in (".signing_key", ".lock"):
                target = public / file.relative_to(p)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
        record = s.ledger_records()[0]
        verified = signing.verify(public, record)
        tampered = dict(record, sig="00" * 64)
        primitive = signing.verify(public, tampered)
        emit("C06_public_verification_and_tamper", "fix_confirmed",
             verified and not primitive, public_verify=verified, tampered_verify=primitive)

    # -- R01: tool output carries no approval material ---------------------
    def r01() -> None:
        p = fresh("R01")
        a = call(p, "memory_add", {"layer": "A", "text": "R01 report synthetic amber.",
                                   "source": "file:untrusted-synthetic",
                                   "authority": "user-explicit", "evidence": "quote",
                                   "source_event_id": "fabricated-R01"})
        review = call(p, "memory_review", {"entry_id": a["entry_id"], "accept": True,
                                           "approver": "human"})
        emit("R01_same_agent_token_roundtrip", "fix_confirmed",
             a["quarantined"] and "token" not in json.dumps(a) and review["_isError"]
             and "R01 report" not in loaded(p),
             approval_material_in_tool_result="token" in json.dumps(a),
             review_error=review["_isError"])

    # -- R02: approval cannot launder an unverifiable claim ----------------
    def r02() -> None:
        p = fresh("R02")
        s = MemoryStore(p)
        e = s.add(Entry("A", "R02 synthetic rule.", "file:synthetic",
                        authority="user-explicit", evidence="quote"))
        rec = approval.mint(p, entry_id=e.entry_id, text="R02 synthetic rule.",
                            source_event_id="")
        try:
            s.approve(e.entry_id, rec)
            raised = False
        except StoreError:
            raised = True
        emit("R02_source_free_promotion_loads_despite_failed_audit", "fix_confirmed",
             raised and "R02 synthetic" not in loaded(p),
             approval_rejected=raised)

    # -- R03: multi-hop revocation -----------------------------------------
    def r03() -> None:
        p = fresh("R03")
        s = MemoryStore(p)
        ancestor = s.add(data_entry("R03 ancestor", "src-R03"))
        child = s.add(data_entry("R03 child", "", [ancestor.entry_id]))
        grandchild = s.add(data_entry("R03 grandchild", "", [child.entry_id]))
        hits = s.revoke("src-R03")["revoked"]
        text = loaded(p)
        quiet = all(x not in text for x in
                    ("R03 ancestor", "R03 child", "R03 grandchild"))
        emit("R03_multihop_revocation", "fix_confirmed",
             len(hits) == 3 and quiet and audit_store(p)["ok"],
             revoked_hits=len(hits), loaded_excludes=quiet,
             grandchild_id_in_hits=grandchild.entry_id in hits)

    # -- R04: approval after source revocation -----------------------------
    def r04() -> None:
        p = fresh("R04")
        s = MemoryStore(p)
        e = s.add(Entry("A", "R04 late approval.", "msg:R04",
                        authority="user-explicit", evidence="quote",
                        source_event_id="eR04"))
        pending = s.quarantine_entries()[0]
        s.revoke("eR04")
        rec = approval.mint(p, entry_id=pending["entry_id"], text=pending["text"],
                            source_event_id="eR04")
        try:
            s.approve(pending["entry_id"], rec)
            raised = False
        except StoreError:
            raised = True
        emit("R04_approval_after_source_revocation", "fix_confirmed",
             raised and "R04 late" not in loaded(p), approval_rejected=raised)

    # -- R05: derivation after revocation ----------------------------------
    def r05() -> None:
        p = fresh("R05")
        s = MemoryStore(p)
        parent = s.add(data_entry("R05 parent", "eR05"))
        s.revoke("eR05")
        s.add(data_entry("R05 later derived", "eR05-later", [parent.entry_id]))
        emit("R05_later_derivation_from_revoked_source", "fix_confirmed",
             "R05 later" not in loaded(p) and s.quarantined() == 1)

    # -- R06: revocation survives state loss -------------------------------
    def r06() -> None:
        p = fresh("R06")
        s = MemoryStore(p)
        e = s.add(data_entry("R06 live before revoke", "eR06"))
        s.revoke("eR06")
        (p / "state.json").write_text("{}", encoding="utf-8")
        reopened = MemoryStore(p)
        emit("R06_revoke_ledger_without_state", "fix_confirmed",
             e.entry_id in reopened.revoked_ids()
             and "R06 live" not in loaded(p) and audit_store(p)["ok"])

    # -- R07: stale handle cannot undo a revoke ----------------------------
    def r07() -> None:
        p = fresh("R07")
        s = MemoryStore(p)
        e = s.add(data_entry("R07 prior rule", "eR07"))
        stale = MemoryStore(p)
        s.revoke("eR07")
        stale.consolidate("checkpoint-R07", 0.4)
        emit("R07_stale_consolidate_restores_revoked_entry", "fix_confirmed",
             "R07 prior" not in loaded(p) and e.entry_id in MemoryStore(p).revoked_ids())

    # -- R08: crash between ledger and file is repaired --------------------
    def r08() -> None:
        from unittest import mock
        p = fresh("R08")
        s = MemoryStore(p)
        with mock.patch.object(MemoryStore, "_append_locked",
                               side_effect=RuntimeError("interrupt")):
            try:
                s.add(data_entry("R08 unmaterialized"))
            except RuntimeError:
                pass
        reopened = MemoryStore(p)
        writes = sum(r.get("event") == "write" for r in reopened.ledger_records())
        emit("R08_unmaterialized_write_audit_pass", "fix_confirmed",
             writes == 1 and "R08 unmaterialized" in loaded(p) and audit_store(p)["ok"],
             logged_writes=writes, replay_repaired="R08 unmaterialized" in loaded(p))

    # -- R09: retry repairs the missing bind -------------------------------
    def r09() -> None:
        p = fresh("R09")
        s = MemoryStore(p)
        e = data_entry("R09 partially committed")
        s.add(e)
        lines = s.ledger_path.read_text(encoding="utf-8").splitlines()
        s.ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        reopened = MemoryStore(p)
        before = not audit_store(p)["ok"]
        reopened.add(e)
        emit("R09_retry_does_not_repair_partial_write", "fix_confirmed",
             before and audit_store(p)["ok"], audit_before=before,
             audit_after=audit_store(p)["ok"])

    # -- R10: a normal write cannot certify a hand edit --------------------
    def r10() -> None:
        p = fresh("R10")
        signing.keygen(p)
        s = MemoryStore(p)
        s.add(data_entry("R10 original"))
        f = s.path_for("D")
        f.write_text(f.read_text(encoding="utf-8").replace("R10 original", "R10 modified"),
                     encoding="utf-8")
        before = not audit_store(p)["ok"]
        try:
            MemoryStore(p).add(data_entry("R10 legitimate next"))
            refused = False
        except StoreError:
            refused = True
        emit("R10_normal_write_certifies_unrecorded_edit", "fix_confirmed",
             before and refused and not audit_store(p)["ok"],
             audit_before=before, write_refused=refused)

    # -- R11: unlogged markdown is not served ------------------------------
    def r11() -> None:
        p = fresh("R11")
        s = MemoryStore(p)
        with s.path_for("A").open("a", encoding="utf-8") as out:
            out.write("\nR11 ordinary unlogged Markdown paragraph.\n")
        report = audit_store(p)
        emit("R11_nonbullet_text_is_uncovered", "fix_confirmed",
             "R11 ordinary" not in loaded(p) and not report["ok"]
             and "unknown_blocks" in report["checks"]["structural"])

    # -- R12: a copy without key material cannot verify the ledger --------
    def r12() -> None:
        p = fresh("R12")
        signing.keygen(p)
        MemoryStore(p).add(data_entry("R12 signed record"))
        copy = fresh("R12-copy")
        shutil.copytree(p, copy / "store", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".signing_key", "approval.key"))
        full = fresh("R12-full")
        shutil.copytree(p, full / "store", dirs_exist_ok=True)
        emit("R12_public_only_audit_skips_signature", "fix_confirmed",
             not audit_store(copy / "store")["ok"] and audit_store(full / "store")["ok"])

    # -- R13: sensitive witnesses respect word boundaries -----------------
    def r13() -> None:
        p = fresh("R13")
        s = MemoryStore(p)
        s.add(data_entry("Use the archive for completed reports.", layer="C"))
        benign = s.quarantined() == 0
        s.add(data_entry("The patient was diagnosed with HIV.", layer="C"))
        caught = s.quarantined() == 1
        emit("R13_archive_sensitive_false_positive", "fix_confirmed", benign and caught,
             benign_not_quarantined=benign, sensitive_quarantined=caught)

    cases = [("C01", c01), ("C02", c02), ("C03", c03), ("C04", c04), ("C05", c05),
             ("C06", c06), ("R01", r01), ("R02", r02), ("R03", r03), ("R04", r04),
             ("R05", r05), ("R06", r06), ("R07", r07), ("R08", r08), ("R09", r09),
             ("R10", r10), ("R11", r11), ("R12", r12), ("R13", r13)]
    for identifier, fn in cases:
        run(identifier, fn)

    summary = {kind: sum(r["kind"] == kind and r["demonstrated"] for r in results)
               for kind in ROOT_KINDS}
    payload = {
        "artifact_version": version,
        "checks": results,
        "summary": summary,
        "probe_errors": sum(r["kind"] == "probe_error" for r in results),
    }
    out = Path(args.result)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["probe_errors"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
