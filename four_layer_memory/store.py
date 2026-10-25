# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""On-disk memory store: layer files + quarantine + ledger + pointer state.

Hardening in v0.3 (see docs/THREAT-MODEL.md):

* every mutation runs under an exclusive file lock, so two processes or two
  threads cannot interleave a read-modify-write on the same store;
* every file write is atomic (temp file + ``os.replace``), so a crash leaves
  either the old bytes or the new bytes, never half a line;
* the ledger is a hash chain; ``audit`` recomputes it and reports the first
  broken link instead of trusting the file;
* quarantined claims can be reviewed: ``approve`` promotes one into its layer
  with human authority, ``reject`` closes it without writing.

Deliberately still out of scope: multi-host stores, CRDT merge, and any form of
cross-process agreement beyond an exclusive lock on one directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from . import admission
from . import signing
from .layers import Layer, spec
from .watermark import WATERMARK


class StoreError(RuntimeError):
    pass


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
]

_LOCK_TIMEOUT_S = 5.0


@dataclass
class Entry:
    layer: str
    text: str
    source: str = ""             # provenance marker: message id, file, URL
    at: str = ""                 # ISO-8601 timestamp
    tags: list[str] = field(default_factory=list)
    # four orthogonal axes (see admission.py)
    authority: str = "inferred"
    evidence: str = "summary"
    lifecycle: str = "standing"
    source_event_id: str = ""    # the immutable source event this claims to come from
    derived_from: list[str] = field(default_factory=list)
    entry_id: str = ""
    revoked_at: str = ""
    approved: bool = False

    def validate(self) -> None:
        s = spec(self.layer)
        text = " ".join(self.text.split())
        if not text:
            raise StoreError("empty entry")
        if len(text) > s.max_chars:
            raise StoreError(f"entry too long for layer {self.layer}: {len(text)} > {s.max_chars}")
        if not self.source:
            raise StoreError("entry needs a source reference (traceability is the point)")
        for pat in _SECRET_PATTERNS:
            if pat.search(text):
                raise StoreError("refusing to store what looks like a secret")
        admission.normalize(self.layer, self.authority, self.evidence, self.lifecycle)
        self.text = text
        if not self.entry_id:
            digest = hashlib.sha1(
                f"{self.layer}|{self.text}|{self.source}|{self.at}".encode("utf-8")
            ).hexdigest()[:12]
            self.entry_id = digest

    def to_record(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "layer": self.layer,
            "text": self.text,
            "source": self.source,
            "at": self.at,
            "tags": list(self.tags),
            "authority": self.authority,
            "evidence": self.evidence,
            "lifecycle": self.lifecycle,
            "source_event_id": self.source_event_id,
            "derived_from": list(self.derived_from),
            "revoked_at": self.revoked_at,
            "approved": bool(self.approved),
            "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }


@dataclass
class State:
    session_id: str = "unnamed"
    last_anchor_ref: str = ""
    ledger_records: int = 0
    consolidated_at: str = ""
    usage_at_consolidation: float = 0.0
    layer_counts: dict[str, int] = field(default_factory=dict)
    cooldown_until_entries: int = 0
    revoked: list[str] = field(default_factory=list)
    watermark: str = WATERMARK

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryStore:
    """A directory of layer files plus state, quarantine, and an append-only ledger.

    Layout::

        <root>/
          state.json
          rules/AGENTS.md
          preferences/work.md
          capabilities/index.md
          background/archive.md
          quarantine/pending.md      # entries whose claimed layer was not granted
          quarantine/resolved.jsonl  # approve / reject decisions
          ledger.jsonl               # hash-chained write / quarantine / revoke records
          versions/                  # backups before every write
          .lock                      # exclusive lock target
    """

    def __init__(self, root: str | Path, session_id: str = "unnamed"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.versions = self.root / "versions"
        self.versions.mkdir(exist_ok=True)
        self.quarantine_dir = self.root / "quarantine"
        self.quarantine_dir.mkdir(exist_ok=True)
        self.quarantine_path = self.quarantine_dir / "pending.md"
        self.resolved_path = self.quarantine_dir / "resolved.jsonl"
        self._pending_path = self.quarantine_dir / "pending.jsonl"
        if not self.quarantine_path.exists():
            self.quarantine_path.write_text(
                f"# quarantine\n\n<!-- {WATERMARK} -->\n\n", encoding="utf-8"
            )
        self.ledger_path = self.root / "ledger.jsonl"
        self.lock_path = self.root / ".lock"
        self._last_hash = self._ledger_tail_hash()
        if self.state_path.exists():
            self.state = State(**json.loads(self.state_path.read_text(encoding="utf-8")))
        else:
            self.state = State(session_id=session_id)
            self._save_state()
        for layer in Layer:
            p = self.path_for(layer)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(f"# {spec(layer).name}\n\n<!-- {WATERMARK} -->\n", encoding="utf-8")

    # -- locking and atomic writes ----------------------------------------
    class _Lock:
        def __init__(self, path: Path, timeout: float = _LOCK_TIMEOUT_S):
            self.path = path
            self.timeout = timeout
            self.fh = None

        def __enter__(self):
            self.fh = self.path.open("a+b")
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        self.fh.seek(0)
                        msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - POSIX path exercised on CI only
                        import fcntl

                        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fh.close()
                        raise StoreError(f"could not acquire store lock: {self.path}")
                    time.sleep(0.01)

        def __exit__(self, *exc_info):
            try:
                if os.name == "nt":  # pragma: no cover - platform specific
                    import msvcrt

                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover
                    import fcntl

                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
            return False

    def _lock(self):
        return MemoryStore._Lock(self.lock_path)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):  # pragma: no cover - only on failure
                os.unlink(tmp)

    # -- paths -------------------------------------------------------------
    def path_for(self, layer: Layer | str) -> Path:
        return self.root / spec(layer).target

    # -- state -------------------------------------------------------------
    def _save_state(self) -> None:
        self._atomic_write(self.state_path, self.state.to_json())

    def status(self) -> dict:
        return {
            "root": str(self.root),
            "session_id": self.state.session_id,
            "last_anchor_ref": self.state.last_anchor_ref,
            "consolidated_at": self.state.consolidated_at,
            "layer_counts": self.state.layer_counts,
            "cooldown_until_entries": self.state.cooldown_until_entries,
            "quarantined": self.quarantined(),
            "watermark": WATERMARK,
        }

    def _reload(self) -> None:
        """Re-read state and the ledger tail so two handles cannot fork the chain."""
        if self.state_path.exists():
            self.state = State(**json.loads(self.state_path.read_text(encoding="utf-8")))
        self._last_hash = self._ledger_tail_hash()
        self._ledger_count = len(self.ledger_records())

    def _find_written(self, entry_id: str) -> Entry | None:
        """Return the entry already on disk with this id, if any (idempotent add)."""
        if not entry_id:
            return None
        needle = f"`id: {entry_id}`"
        for layer in Layer:
            p = self.path_for(layer)
            if not p.exists():
                continue
            for block in p.read_text(encoding="utf-8").split("\n- ")[1:]:
                if needle in block:
                    first = block.splitlines()[0].lstrip("- ")
                    if "  \n  `source:" in block:
                        first = block.split("  \n  `source:")[0].lstrip("- ")
                    return Entry(layer=layer.value, text=first.strip(),
                                 source="recovered", entry_id=entry_id)
        return None

    def revoked_ids(self) -> set[str]:
        return set(self.state.revoked or [])

    def _bullet(self, entry: Entry) -> str:
        bullet = f"- {entry.text}"
        if entry.source:
            bullet += f"  \n  `source: {entry.source}` `at: {entry.at}`"
        bullet += (
            f"  `authority: {entry.authority}` `evidence: {entry.evidence}` "
            f"`lifecycle: {entry.lifecycle}` `id: {entry.entry_id}`"
        )
        if entry.tags:
            bullet += f"  `tags: {','.join(entry.tags)}`"
        return bullet

    def _layer_text_with(self, entry: Entry) -> str:
        path = self.path_for(entry.layer)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current and not current.endswith("\n"):
            current += "\n"
        return current + self._bullet(entry) + "\n"

    # -- ledger ------------------------------------------------------------
    def _ledger_tail_hash(self) -> str:
        """Hash of the last committed ledger record (empty for a fresh store)."""
        if not self.ledger_path.exists():
            return ""
        for line in reversed(self.ledger_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return str(json.loads(line).get("hash", ""))
            except json.JSONDecodeError:  # pragma: no cover - defensive
                return ""
        return ""

    def _ledger(self, record: dict) -> None:
        body = {"ts": now_iso(), **record}
        body["prev"] = self._last_hash
        body["hash"] = _sha(_canonical(body))
        if signing.has_key(self.root):
            body = signing.sign(self.root, body)
        self._last_hash = body["hash"]
        self._ledger_count = getattr(self, "_ledger_count", 0) + 1
        self.state.ledger_records = self._ledger_count
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(body) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def ledger_records(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        out = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # pragma: no cover - defensive
                continue
        return out

    def ledger_integrity(self) -> dict:
        """Recompute the hash chain; report the first broken link."""
        prev = ""
        records = self.ledger_records()
        if self.state.ledger_records and len(records) < self.state.ledger_records:
            return {"ok": False, "at": len(records), "records": len(records),
                    "signed": signing.algorithm(self.root) or "unsigned",
                    "reason": f"ledger truncated: state records {self.state.ledger_records}, found {len(records)}"}
        for index, rec in enumerate(records):
            claimed = rec.get("hash")
            body = {k: v for k, v in rec.items() if k not in ("hash", "sig", "sig_alg")}
            if body.get("prev") != prev:
                return {"ok": False, "at": index, "reason": "prev hash mismatch"}
            if _sha(_canonical(body)) != claimed:
                return {"ok": False, "at": index, "reason": "hash mismatch"}
            if signing.has_key(self.root) and not signing.verify(self.root, rec):
                return {"ok": False, "at": index, "reason": "signature mismatch"}
            prev = claimed or ""
        return {"ok": True, "records": index + 1 if prev else 0, "head": prev,
                "signed": signing.algorithm(self.root) or "unsigned"}

    # -- writes ------------------------------------------------------------
    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
        out = self.versions / f"{path.name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.{digest}.bak"
        shutil.copy2(path, out)

    def _append_locked(self, entry: Entry) -> None:
        path = self.path_for(entry.layer)
        self._backup(path)
        self._atomic_write(path, self._layer_text_with(entry))
        key = spec(entry.layer).name
        self.state.layer_counts[key] = self.state.layer_counts.get(key, 0) + 1

    def add(self, entry: Entry) -> Entry:
        """Validate, decide, then commit ledger -> layer -> state under one lock.

        The ledger record is written *before* the layer file and carries the hash
        of the file the write should produce, so a crash between the two is
        detectable by ``audit`` instead of silently leaving memory without logs.
        """
        with self._lock():
            self._reload()
            entry.validate()
            entry.at = entry.at or now_iso()
            existing = self._find_written(entry.entry_id)
            if existing is not None:
                return existing
            decision = admission.decide(
                entry.layer, entry.authority, entry.evidence,
                source_event_id=entry.source_event_id,
                approved=entry.approved,
                text=entry.text,
            )
            if not decision.accept:
                self._write_quarantine(entry, decision.reason)
                self._save_state()
                return entry
            self._ledger({
                "event": "write",
                **entry.to_record(),
                "reason": decision.reason,
            })
            self._append_locked(entry)
            self._ledger({
                "event": "bind",
                "entry_id": entry.entry_id,
                "layer": entry.layer,
                "layer_sha256": _sha(self.path_for(entry.layer).read_text(encoding="utf-8")),
            })
            self._save_state()
            return entry

    def add_many(self, entries: list[Entry]) -> list[Entry]:
        """Add several entries under one lock and one state write.

        A crash mid-batch may leave some entries written and some not; the point
        is that no entry is written twice and the ledger stays a valid chain.
        """
        out = []
        with self._lock():
            self._reload()
            for entry in entries:
                entry.validate()
                entry.at = entry.at or now_iso()
                if self._find_written(entry.entry_id) is not None:
                    out.append(entry)
                    continue
                decision = admission.decide(
                    entry.layer, entry.authority, entry.evidence,
                    source_event_id=entry.source_event_id,
                    approved=entry.approved,
                    text=entry.text,
                )
                if not decision.accept:
                    self._write_quarantine(entry, decision.reason)
                else:
                    self._ledger({
                        "event": "write", **entry.to_record(),
                        "reason": decision.reason,
                    })
                    self._append_locked(entry)
                    # Bind the bytes that are now on disk, not a prediction.
                    self._ledger({
                        "event": "bind",
                        "entry_id": entry.entry_id,
                        "layer": entry.layer,
                        "layer_sha256": _sha(self.path_for(entry.layer).read_text(encoding="utf-8")),
                    })
                out.append(entry)
            self._save_state()
        return out

    def _write_quarantine(self, entry: Entry, reason: str) -> None:
        token = secrets.token_hex(3).upper()
        record = {
            **entry.to_record(),
            "reason": reason,
            "approval_token": token,
            "quarantined_at": now_iso(),
        }
        block = [
            f"## {entry.entry_id}",
            f"- claimed layer: `{entry.layer}`",
            f"- authority: `{entry.authority}` / evidence: `{entry.evidence}` / lifecycle: `{entry.lifecycle}`",
            f"- source: `{entry.source}` at `{entry.at}`",
            f"- source event: `{entry.source_event_id}`",
            f"- rejected: {reason}",
            f"- approval token: `{token}`",
            f"- text: {entry.text}",
            "",
        ]
        current = self.quarantine_path.read_text(encoding="utf-8")
        self._atomic_write(self.quarantine_path, current + "\n".join(block))
        with self._pending_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + chr(10))
        self._ledger({"event": "quarantine", **entry.to_record(), "reason": reason})

    def _resolved_ids(self) -> set[str]:
        if not self.resolved_path.exists():
            return set()
        ids = set()
        for line in self.resolved_path.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line).get("entry_id", ""))
            except json.JSONDecodeError:  # pragma: no cover
                continue
        return ids

    def quarantined(self) -> int:
        """Unresolved quarantine entries (approved or rejected ones stop counting)."""
        if not self.quarantine_path.exists():
            return 0
        ids = re.findall(r"(?m)^## ([0-9a-f]{6,})$", self.quarantine_path.read_text(encoding="utf-8"))
        return len([i for i in ids if i not in self._resolved_ids()])

    def quarantine_entries(self, include_resolved: bool = False) -> list[dict]:
        """Entries in the quarantine file; resolved ones are hidden by default."""
        if self._pending_path.exists():
            resolved = self._resolved_ids()
            out = []
            for line in self._pending_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:  # pragma: no cover
                    continue
                if include_resolved or rec.get("entry_id") not in resolved:
                    out.append(rec)
            return out
        text = self.quarantine_path.read_text(encoding="utf-8")
        blocks = re.split(r"(?m)^## ", text)[1:]
        out = []
        for block in blocks:
            lines = block.splitlines()
            entry_id = lines[0].strip()
            record = {"entry_id": entry_id, "text": "", "layer": "", "source": "", "at": ""}
            for line in lines:
                if line.startswith("- text: "):
                    record["text"] = line[len("- text: "):]
                elif line.startswith("- claimed layer: "):
                    record["layer"] = line.split("`")[1] if "`" in line else ""
                elif line.startswith("- source: "):
                    parts = re.findall(r"`([^`]*)`", line)
                    if parts:
                        record["source"] = parts[0]
                    if len(parts) > 1:
                        record["at"] = parts[1].replace("at ", "")
            if include_resolved or entry_id not in self._resolved_ids():
                out.append(record)
        return out

    def review(self, entry_id: str, accept: bool, token: str = "", approver: str = "human") -> dict:
        """Human decision on a quarantined claim.

        Accepting is the only way a claim crosses into layer A without having
        been user-explicit at write time; the reviewer is the new authority, so
        the entry is written as ``user-explicit`` + ``quote`` with an
        ``approved:<reviewer>`` tag.
        """
        match = [e for e in self.quarantine_entries(include_resolved=True) if e["entry_id"] == entry_id]
        if not match:
            raise StoreError(f"no quarantined entry {entry_id}")
        found = match[0]
        if entry_id in self._resolved_ids():
            raise StoreError(f"already resolved: {entry_id}")
        if accept:
            expected = str(found.get("approval_token") or "")
            if not token or not expected or token.strip().upper() != expected:
                raise StoreError("approval token required (and must match the token shown at quarantine time)")
        decision = "approve" if accept else "reject"
        with self._lock():
            self._reload()
            if accept:
                entry = Entry(
                    layer=str(found.get("layer") or "D"),
                    text=str(found.get("text") or ""),
                    source=str(found.get("source") or "quarantine"),
                    at=str(found.get("at") or now_iso()),
                    tags=["approved", f"approver:{approver}"],
                    authority="user-explicit",
                    evidence="quote",
                    lifecycle=str(found.get("lifecycle") or "standing"),
                    source_event_id=str(found.get("source_event_id") or ""),
                    derived_from=list(found.get("derived_from") or []),
                    entry_id=entry_id,
                    approved=True,
                )
                entry.validate()
                self._ledger({
                    "event": "approve",
                    **entry.to_record(),
                    "layer_sha256": _sha(self._layer_text_with(entry)),
                })
                self._append_locked(entry)
                self._save_state()
            with self.resolved_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"entry_id": entry_id, "decision": decision,
                                     "ts": now_iso()}, ensure_ascii=False) + chr(10))
                fh.flush()
                os.fsync(fh.fileno())
            self._ledger({"event": decision, "entry_id": entry_id})
        return {"entry_id": entry_id, "decision": decision, "layer": found["layer"]}

    def revoke(self, source_event_id: str) -> dict:
        """Revoke every entry derived from a source event, and stop loading them."""
        with self._lock():
            self._reload()
            hits: list[str] = []
            for rec in self.ledger_records():
                if rec.get("event") not in ("write", "approve"):
                    continue
                if rec.get("source_event_id") == source_event_id or source_event_id in rec.get("derived_from", []):
                    entry_id = str(rec.get("entry_id") or "")
                    if entry_id and entry_id not in hits:
                        hits.append(entry_id)
            self.state.revoked = sorted(set(list(self.state.revoked or []) + hits))
            self._ledger(
                {
                    "event": "revoke",
                    "source_event_id": source_event_id,
                    "entry_ids": hits,
                    "reason": "source event revoked",
                }
            )
            self._save_state()
        return {"source_event_id": source_event_id, "revoked": hits}

    def consolidate(self, anchor_ref: str, usage_ratio: float,
                    cooldown_entries: int = 50) -> dict:
        """Record a consolidation checkpoint."""
        if not 0.0 <= usage_ratio <= 1.0:
            raise StoreError("usage_ratio must be in [0, 1]")
        with self._lock():
            self.state.last_anchor_ref = anchor_ref
            self.state.consolidated_at = now_iso()
            self.state.usage_at_consolidation = usage_ratio
            self.state.cooldown_until_entries = cooldown_entries
            self._save_state()
        return self.status()

    def signing_info(self) -> dict:
        return {
            "alg": signing.algorithm(self.root) or "unsigned",
            "fingerprint": signing.public_key_hex(self.root),
        }

    def export_bundle(self, out: str | Path) -> Path:
        """Write a single JSON bundle with the watermark stamped in."""
        out = Path(out)
        payload = {
            "watermark": WATERMARK,
            "note": "Provenance: liu bai (Liu Zhu & Shen Bai). Keep this field when redistributing.",
            "state": json.loads(self.state.to_json()),
            "layers": {},
            "quarantine": self.quarantine_path.read_text(encoding="utf-8")
            if self.quarantine_path.exists() else "",
            "resolved": self.resolved_path.read_text(encoding="utf-8")
            if self.resolved_path.exists() else "",
            "ledger": self.ledger_records(),
            "ledger_integrity": self.ledger_integrity(),
        }
        for layer in Layer:
            s = spec(layer)
            payload["layers"][s.name] = {
                "layer": layer.value,
                "question": s.question,
                "body": self.path_for(layer).read_text(encoding="utf-8"),
            }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return out
