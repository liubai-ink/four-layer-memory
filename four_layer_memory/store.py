# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""On-disk memory store: layer files + quarantine + ledger + pointer state.

v0.7 hardening (see docs/THREAT-MODEL.md and docs/REVIEW-RESPONSE-v04.md):

* **host-held approval** - the write side can only request an admission
  decision; moving a claim into layer A requires a signed approval record
  minted by the host channel (``four_layer_memory/approval.py``), and every path
  into A runs the same ``admission.decide`` gate (F01);
* **durable, transitive revocation** - revocations are ledger tombstones; the
  revoked set is recomputed from the ledger as a closure over
  ``source_event_id`` and ``derived_from`` on every load, and ``consolidate``
  reloads state before writing, so a stale handle cannot resurrect a revoked
  entry (F02);
* **crash recovery** - every write is logged as an intent with the exact text it
  should produce, then bound; opening the store repairs an unbound transaction
  instead of leaving memory without a log, and retries stay idempotent (F03);
* **ledger-derived audit** - the expected bytes of every layer file are rebuilt
  from the ledger, so a hand edit cannot be laundered by a later write, and the
  loader only serves records that follow the documented bullet grammar (F04);
* **signature honesty** - a signed ledger is not "verified" when no key material
  is available (F05).

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
from datetime import date, datetime, timezone
from pathlib import Path

from . import admission
from . import approval
from . import archive as archive_mod
from . import signing
from .layers import Layer, spec
from .watermark import WATERMARK

STORE_FORMAT = "0.7.0"
DEFAULT_COOLDOWN_ENTRIES = 8


class StoreError(RuntimeError):
    pass


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
]

_META_RE = re.compile(r"`([A-Za-z_]+): ([^`]*)`")
_ID_RE = re.compile(r"`id: ([^`]+)`")

_LOCK_TIMEOUT_S = 5.0


def _canonical(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def _parse_when(value: str):
    """Parse an ISO date or datetime; returns None when unparseable."""
    if not value:
        return None
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_expired(value: str) -> bool:
    when = _parse_when(value)
    if when is None:
        # An unparseable expiry fails closed: better to stop serving it than to
        # guess how long the claim was meant to last.
        return bool(value)
    now = datetime.now(timezone.utc)
    if isinstance(when, datetime):
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return now >= when
    return now.date() > when


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    # lifecycle coordinates (v0.7 F03): scope and expiry are part of the entry,
    # not a comment in the prose, so the loader can enforce them.
    scope: str = ""
    expires_at: str = ""

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
        if self.expires_at and _parse_when(self.expires_at) is None:
            raise StoreError(f"unparseable expiry date: {self.expires_at!r} (use ISO-8601)")
        if self.scope and len(self.scope) > 120:
            raise StoreError("scope must be a short identifier (<= 120 chars)")
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
            "scope": self.scope,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "approved": bool(self.approved),
            "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }

    @classmethod
    def from_record(cls, rec: dict) -> "Entry":
        rec = rec or {}
        return cls(
            layer=str(rec.get("layer", "")),
            text=str(rec.get("text", "")),
            source=str(rec.get("source", "")),
            at=str(rec.get("at", "")),
            tags=list(rec.get("tags") or []),
            authority=str(rec.get("authority", "inferred")),
            evidence=str(rec.get("evidence", "summary")),
            lifecycle=str(rec.get("lifecycle", "standing")),
            source_event_id=str(rec.get("source_event_id", "")),
            derived_from=list(rec.get("derived_from") or []),
            entry_id=str(rec.get("entry_id", "")),
            revoked_at=str(rec.get("revoked_at", "")),
            approved=bool(rec.get("approved", False)),
            scope=str(rec.get("scope", "")),
            expires_at=str(rec.get("expires_at", "")),
        )


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
    recovered: list[dict] = field(default_factory=list)
    # F01: durable record count *and* head hash.  Recovery must never lower
    # either value silently; a mismatch means the ledger was truncated or
    # rewound outside the store and everything is blocked until restored.
    ledger_head: str = ""
    truncated: dict = field(default_factory=dict)
    # v0.7.2: mirror of the local raw archive (never served, opt-in recall).
    archive_events: int = 0
    archive_last_sha256: str = ""
    archive_error: str = ""
    store_version: str = STORE_FORMAT
    watermark: str = WATERMARK

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"


_STATE_FIELDS = set(State.__dataclass_fields__)


def _state_from_dict(payload: dict) -> State:
    known = {k: v for k, v in (payload or {}).items() if k in _STATE_FIELDS}
    return State(**known)


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
          ledger.jsonl               # hash-chained write / bind / approve / revoke records
          versions/                  # backups before every write
          .lock                      # exclusive lock target
          .signing_key               # ledger signing material (optional)
          approval.key               # host approval key (created by the host channel)
    """

    def __init__(self, root: str | Path, session_id: str = "unnamed", alg: str | None = None,
                 recover: bool = True):
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
        if not self.ledger_path.exists():
            self.ledger_path.write_text("", encoding="utf-8")
        self.lock_path = self.root / ".lock"
        self.archive = archive_mod.Archive(self.root)
        if alg:
            if not signing.has_key(self.root):
                signing.keygen(self.root, alg=alg)
        self._last_hash = self._ledger_tail_hash()
        if self.state_path.exists():
            self.state = _state_from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        else:
            self.state = State(session_id=session_id)
            self._save_state()
        for layer in Layer:
            p = self.path_for(layer)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                self._atomic_write(p, self._header(layer))
        records = self.ledger_records()
        self._ledger_count = len(records)
        # F01: a record count that shrinks, or a head hash that no longer sits at
        # the end of the chain, is never repaired away.  The store keeps the
        # durable claim (so audit keeps failing) and refuses writes/loads until
        # the operator restores the ledger from a backup.
        self.state.truncated = self._detect_truncation(records) if self.state_path.exists() else {}
        if not self.state.truncated:
            self.state.ledger_records = self._ledger_count
            self.state.ledger_head = self._ledger_tail_hash()
        # Repair any transaction left half-done by a crash; this is the only
        # place where the store writes without being asked to.  ``recover=False``
        # is used by the audit, which must report, not repair (v0.7 F03/F04).
        if recover:
            self.recover()
        self._reload()

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

    @staticmethod
    def _header(layer: Layer | str) -> str:
        return f"# {spec(layer).name}\n\n<!-- {WATERMARK} -->\n"

    # -- state -------------------------------------------------------------
    def _save_state(self) -> None:
        self._atomic_write(self.state_path, self.state.to_json())

    def _reload(self) -> None:
        """Re-read state and the ledger tail so two handles cannot fork the chain."""
        if self.state_path.exists():
            self.state = _state_from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        self._last_hash = self._ledger_tail_hash()
        self._ledger_count = len(self.ledger_records())

    # -- ledger health (F01) ------------------------------------------------
    def _detect_truncation(self, records: list[dict]) -> dict:
        """Compare the durable claim in state.json with the ledger on disk.

        Returns a non-empty dict when the ledger is shorter than claimed, its
        head no longer sits at the end, or the record count disagrees.  An empty
        dict means the two agree (or the store predates head tracking).
        """
        n = len(records)
        exp_n = int(getattr(self.state, "ledger_records", 0) or 0)
        exp_head = str(getattr(self.state, "ledger_head", "") or "")
        head = str(records[-1].get("hash", "")) if records else ""
        info: dict = {}
        if exp_n > n:
            info = {"reason": f"ledger truncated: state records {exp_n}, found {n}",
                    "kind": "truncated"}
        elif exp_head and head != exp_head:
            # Appends after a state save are normal: the recorded head is then
            # an ancestor of the current tail.  Only a *missing* head means the
            # ledger was rewound or replaced.
            found = any(str(rec.get("hash", "")) == exp_head for rec in records)
            if not found:
                info = {"reason": "ledger rewound: the recorded head is not in the chain",
                        "kind": "rewound"}
        elif exp_n and exp_n != n:
            info = {"reason": f"record count mismatch: state {exp_n}, found {n}",
                    "kind": "count"}
        if info:
            info.update({"expected_records": exp_n, "found_records": n,
                         "expected_head": exp_head, "found_head": head,
                         "detected_at": now_iso()})
        return info

    def _assert_ledger_ok(self) -> None:
        info = dict(getattr(self.state, "truncated", {}) or {})
        if info:
            raise StoreError(
                "ledger integrity check failed: " + str(info.get("reason", "unknown"))
                + ". Restore ledger.jsonl from versions/ or a backup; the store "
                  "refuses reads and writes until the durable claim is met."
            )

    def _rebuild_resolved(self) -> int:
        """Reconstruct quarantine/resolved.jsonl from the authoritative ledger.

        Rejections (``review``) and acceptances (``approve``) are ledger events;
        the side file is a cache and must be derivable from them (F03).
        """
        out: list[dict] = []
        for rec in self.ledger_records():
            if rec.get("event") == "approve" and rec.get("entry_id"):
                out.append({"entry_id": str(rec["entry_id"]), "decision": "approve",
                            "approver": str(rec.get("approver", "host")),
                            "ts": str(rec.get("ts", ""))})
            elif rec.get("event") == "review" and rec.get("entry_id") \
                    and not rec.get("accept", True):
                out.append({"entry_id": str(rec["entry_id"]), "decision": "reject",
                            "approver": str(rec.get("approver", "human")),
                            "ts": str(rec.get("ts", ""))})
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
        self._atomic_write(self.resolved_path, text)
        return len(out)

    def _servable(self, records: list[dict] | None = None) -> list[Entry]:
        """Entries the ledger can prove were committed (write/approve + bind)."""
        records = records if records is not None else self.ledger_records()
        bound = {str(r.get("tx")) for r in records
                 if r.get("event") == "bind" and r.get("tx")}
        out: list[Entry] = []
        for rec in records:
            if rec.get("event") not in ("write", "approve"):
                continue
            tx = str(rec.get("tx") or "")
            if tx and tx not in bound:  # half-committed: never served
                continue
            entry = Entry.from_record(rec)
            if entry.entry_id:
                out.append(entry)
        return out

    def status(self) -> dict:
        ids, sources = self.revoked_sets()
        records = self.ledger_records()
        return {
            "root": str(self.root),
            "store_version": STORE_FORMAT,
            "session_id": self.state.session_id,
            "last_anchor_ref": self.state.last_anchor_ref,
            "consolidated_at": self.state.consolidated_at,
            "usage_at_consolidation": self.state.usage_at_consolidation,
            "layer_counts": dict(self.state.layer_counts),
            "cooldown_until_entries": self.state.cooldown_until_entries,
            "quarantined": self.quarantined(),
            "revoked": sorted(ids),
            "revoked_sources": sorted(sources),
            "recovered": list(self.state.recovered or []),
            "ledger_records": len(records),
            "ledger_head": str(getattr(self.state, "ledger_head", "") or ""),
            "ledger_truncated": dict(getattr(self.state, "truncated", {}) or {}),
            "expired": sum(1 for e in self._servable(records) if self._is_expired(e)),
            "signing": self.signing_info(),
            "approval_fingerprint": approval.fingerprint(self.root),
            "watermark": WATERMARK,
        }

    # -- layer files --------------------------------------------------------
    def _bullet(self, entry: Entry) -> str:
        bullet = f"- {entry.text}"
        if entry.source:
            bullet += f"  \n  `source: {entry.source}` `at: {entry.at}`"
        bullet += (
            f"  `authority: {entry.authority}` `evidence: {entry.evidence}` "
            f"`lifecycle: {entry.lifecycle}` `id: {entry.entry_id}`"
        )
        if entry.scope:
            bullet += f"  `scope: {entry.scope}`"
        if entry.expires_at:
            bullet += f"  `expires: {entry.expires_at}`"
        if entry.tags:
            bullet += f"  `tags: {','.join(entry.tags)}`"
        return bullet

    @staticmethod
    def _append_bullet(text: str, entry: Entry) -> str:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + MemoryStore._bullet_safe(entry) + "\n"

    @staticmethod
    def _bullet_safe(entry: Entry) -> str:
        bullet = f"- {entry.text}"
        if entry.source:
            bullet += f"  \n  `source: {entry.source}` `at: {entry.at}`"
        bullet += (
            f"  `authority: {entry.authority}` `evidence: {entry.evidence}` "
            f"`lifecycle: {entry.lifecycle}` `id: {entry.entry_id}`"
        )
        if entry.scope:
            bullet += f"  `scope: {entry.scope}`"
        if entry.expires_at:
            bullet += f"  `expires: {entry.expires_at}`"
        if entry.tags:
            bullet += f"  `tags: {','.join(entry.tags)}`"
        return bullet

    def _layer_text_with(self, entry: Entry) -> str:
        path = self.path_for(entry.layer)
        current = path.read_text(encoding="utf-8") if path.exists() else self._header(entry.layer)
        return self._append_bullet(current, entry)

    @staticmethod
    def parse_layer_text(text: str) -> tuple[list[tuple[Entry, list[str]]], list[str]]:
        """Parse a layer file into (bullets, unknown_lines).

        The grammar is deliberately small: a bullet starts with ``- `` at the
        left margin and may continue on indented lines holding backticked
        ``key: value`` metadata.  Anything else is *unknown*: the loader will not
        serve it and the audit will report it (v0.7 F04/R11).
        """
        blocks: list[tuple[Entry, list[str]]] = []
        unknown: list[str] = []
        current: dict | None = None

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            meta: dict[str, str] = {}
            for line in current["meta"]:
                for m in _META_RE.finditer(line):
                    meta[m.group(1).strip()] = m.group(2).strip()
            text_ = current["text"]
            tags = [t.strip() for t in (meta.get("tags") or "").split(",") if t.strip()]
            entry = Entry(
                layer=str(current["layer"]),
                text=text_,
                source=meta.get("source", ""),
                at=meta.get("at", ""),
                tags=tags,
                authority=meta.get("authority", "observed"),
                evidence=meta.get("evidence", "summary"),
                lifecycle=meta.get("lifecycle", "standing"),
                entry_id=meta.get("id", ""),
                scope=meta.get("scope", ""),
                expires_at=meta.get("expires", ""),
                approved="approved" in tags,
            )
            blocks.append((entry, list(current["raw"])))
            current = None

        for raw in text.splitlines():
            line = raw.rstrip()
            if line.startswith("- "):
                flush()
                current = {"layer": "", "text": line[2:].strip(), "meta": [], "raw": [raw]}
            elif current is not None and (line.startswith("  ") or line.startswith("\t")):
                current["meta"].append(line.strip())
                current["raw"].append(raw)
            else:
                flush()
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("<!--"):
                    unknown.append(stripped)
        flush()
        return blocks, unknown

    def _parse_layer(self, layer: Layer | str) -> tuple[list[tuple[Entry, list[str]]], list[str]]:
        p = self.path_for(layer)
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        blocks, unknown = self.parse_layer_text(text)
        for entry, _raw in blocks:
            entry.layer = str(layer.value if isinstance(layer, Layer) else layer).upper()
        return blocks, unknown


    # -- typed recall ------------------------------------------------------
    def search(self, query: str, layer: str | None = None,
               include_revoked: bool = False, include_expired: bool = False,
               limit: int = 20) -> list[dict]:
        """Retrieve entries by text, keeping their authority and revocation state.

        This is deliberately *not* similarity search over a transcript: the
        result is a list of typed entries, each carrying who authorised it,
        what evidence backed it, its lifecycle and whether it has been
        retracted.  The caller can therefore tell "what the agent knows" from
        "what someone once said".
        """
        q = (query or "").strip()
        if not q:
            return []
        ql = q.casefold()
        revoked, revoked_sources = self.revoked_sets()
        if layer:
            layers = [str(spec(layer).layer.value)]
        else:
            layers = [l.value for l in Layer]
        lim = max(1, int(limit))
        out: list[dict] = []
        for lname in layers:
            blocks, _unknown = self._parse_layer(lname)
            for entry, _raw in blocks:
                hay = entry.text.casefold()
                if ql not in hay:
                    continue
                is_revoked = bool(
                    (entry.entry_id and entry.entry_id in revoked)
                    or (entry.source_event_id and entry.source_event_id in revoked_sources)
                )
                if is_revoked and not include_revoked:
                    continue
                if not include_expired and self._is_expired(entry):
                    continue
                out.append({
                    "entry_id": entry.entry_id,
                    "layer": lname,
                    "text": entry.text,
                    "source": entry.source,
                    "at": entry.at,
                    "authority": entry.authority,
                    "evidence": entry.evidence,
                    "lifecycle": entry.lifecycle,
                    "scope": entry.scope,
                    "tags": list(entry.tags),
                    "revoked": is_revoked,
                    "matches": hay.count(ql),
                })
        out.sort(key=lambda d: (d["layer"], -d["matches"], d["at"]))
        return out[:lim]

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

    def _ledger(self, record: dict) -> dict:
        body = {"ts": now_iso(), **record}
        body["prev"] = self._last_hash
        body["hash"] = _sha(_canonical(body))
        if signing.has_key(self.root):
            body = signing.sign(self.root, body)
        self._last_hash = body["hash"]
        self._ledger_count = getattr(self, "_ledger_count", 0) + 1
        self.state.ledger_records = self._ledger_count
        self.state.ledger_head = body["hash"]
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(body) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return body

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
        """Recompute the hash chain and verify signatures when material is present."""
        prev = ""
        records = self.ledger_records()
        mat = signing.material(self.root)
        signed = mat.get("alg") or "unsigned"
        if getattr(self.state, "truncated", None):
            return {"ok": False, "at": len(records), "records": len(records),
                    "signed": signed, "truncated": dict(self.state.truncated),
                    "reason": str(self.state.truncated.get("reason", "ledger claim unmet"))}
        if self.state.ledger_records and len(records) < self.state.ledger_records:
            return {"ok": False, "at": len(records), "records": len(records),
                    "signed": signed,
                    "reason": f"ledger truncated: state records {self.state.ledger_records}, found {len(records)}"}
        verified = 0
        for index, rec in enumerate(records):
            claimed = rec.get("hash")
            body = {k: v for k, v in rec.items() if k not in ("hash", "sig", "sig_alg")}
            if body.get("prev") != prev:
                return {"ok": False, "at": index, "reason": "prev hash mismatch", "signed": signed}
            if _sha(_canonical(body)) != claimed:
                return {"ok": False, "at": index, "reason": "hash mismatch", "signed": signed}
            ok, reason = signing.verify_with_material(self.root, rec, mat)
            if not ok:
                return {"ok": False, "at": index, "reason": f"record {index}: {reason}",
                        "signed": signed, "verified_with": reason}
            if rec.get("sig"):
                verified += 1
            prev = claimed or ""
        expected_head = str(getattr(self.state, "ledger_head", "") or "")
        if expected_head and len(records) == int(self.state.ledger_records or 0) \
                and prev != expected_head:
            return {"ok": False, "at": len(records), "records": len(records),
                    "signed": signed, "head": prev, "expected_head": expected_head,
                    "reason": "ledger head does not match the durable claim"}
        return {"ok": True, "records": len(records), "head": prev, "signed": signed,
                "verified": verified,
                "verified_with": "private material" if mat.get("private") else
                                 ("public material" if mat.get("public") else "none")}

    # -- writes ------------------------------------------------------------
    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
        out = self.versions / f"{path.name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.{digest}.bak"
        shutil.copy2(path, out)

    def _counts_from_ledger(self, records: list[dict] | None = None) -> dict[str, int]:
        names = {layer.value: spec(layer).name for layer in Layer}
        counts = {name: 0 for name in names.values()}
        for rec in (records if records is not None else self.ledger_records()):
            if rec.get("event") in ("write", "approve"):
                name = names.get(str(rec.get("layer", "")).upper())
                if name:
                    counts[name] = counts.get(name, 0) + 1
        return counts

    def _physical_counts(self) -> dict[str, int]:
        """Bullets actually present in each layer file (tombstones included).

        ``state.layer_counts`` tracks the file, not the served set: audit's
        structural check compares it with the bullet count, and revocation is a
        tombstone that deliberately stays in the file until ``compact``.
        """
        names = {layer.value: spec(layer).name for layer in Layer}
        counts = {name: 0 for name in names.values()}
        for layer in Layer:
            path = self.path_for(layer)
            if not path.exists():
                continue
            blocks, _unknown = self.parse_layer_text(path.read_text(encoding="utf-8"))
            counts[names[layer.value]] = len(blocks)
        return counts

    def _find_record(self, entry_id: str, records: list[dict] | None = None) -> dict | None:
        for rec in (records if records is not None else self.ledger_records()):
            if rec.get("event") in ("write", "approve") and rec.get("entry_id") == entry_id:
                return rec
        return None

    def _tx_bound(self, tx: str, records: list[dict] | None = None) -> bool:
        if not tx:
            return True
        return any(r.get("event") == "bind" and r.get("tx") == tx
                   for r in (records if records is not None else self.ledger_records()))

    def _dropped_ids(self, records: list[dict] | None = None) -> dict[str, set[str]]:
        """Entry ids physically compacted out of each layer (from ``compact`` events)."""
        out: dict[str, set[str]] = {}
        for rec in (records if records is not None else self.ledger_records()):
            if rec.get("event") != "compact":
                continue
            key = str(rec.get("layer", "")).upper()
            out.setdefault(key, set()).update(str(x) for x in rec.get("dropped_ids") or [])
        return out

    def _layer_from_ledger(self, layer: Layer, records: list[dict],
                           skip_ids: set[str] | None = None) -> str:
        """Rebuild one layer file's bytes from its write/approve records."""
        skipped = skip_ids or set()
        text = self._header(layer)
        for rec in records:
            if rec.get("event") not in ("write", "approve"):
                continue
            if str(rec.get("layer", "")).upper() != layer.value:
                continue
            if rec.get("tx") and not self._tx_bound(str(rec["tx"]), records):
                continue
            if str(rec.get("entry_id")) in skipped:
                continue
            entry = Entry.from_record(rec)
            text = self._append_bullet(text, entry)
        return text

    def expected_layers(self) -> dict[str, str]:
        """Expected bytes of each layer file, rebuilt from the ledger (F04).

        ``compact`` events name entries that were physically dropped from a file;
        they stay in the ledger for replay, so they are skipped here as well.
        """
        records = self.ledger_records()
        dropped = self._dropped_ids(records)
        return {
            layer.value: self._layer_from_ledger(layer, records, dropped.get(layer.value, set()))
            for layer in Layer
        }

    def _append_locked(self, entry: Entry, text: str | None = None) -> None:
        path = self.path_for(entry.layer)
        payload = text if text is not None else self._layer_text_with(entry)
        self._backup(path)
        self._atomic_write(path, payload)
        key = spec(entry.layer).name
        self.state.layer_counts[key] = self.state.layer_counts.get(key, 0) + 1

    def _guard_bytes(self, layer: str, expected: str) -> None:
        """Refuse to write when the layer file is not what the ledger predicts."""
        p = self.path_for(layer)
        actual = p.read_text(encoding="utf-8") if p.exists() else ""
        if _norm(actual) != _norm(expected):
            raise StoreError(
                f"layer file {spec(layer).target} diverges from the ledger; "
                "refusing to write. Run `four-layer-memory audit` and restore the "
                "file from versions/ (the bytes changed outside the store)."
            )

    def _check_idempotent(self, entry: Entry, records: list[dict]) -> Entry | None:
        rec = self._find_record(entry.entry_id, records)
        if rec is None:
            # An entry id that exists on disk but not in the ledger is an orphan:
            # never adopt it silently (v0.7 F04).
            for layer in Layer:
                blocks, _ = self._parse_layer(layer)
                if any(e.entry_id == entry.entry_id for e, _r in blocks):
                    raise StoreError(
                        f"entry {entry.entry_id} exists in {spec(layer).target} but the "
                        "ledger has no record of it; run `four-layer-memory audit`"
                    )
            return None
        if rec.get("tx") and not self._tx_bound(str(rec["tx"]), records):
            self._recover_tx(rec, records)
            records = self.ledger_records()
            # R09/F01: if the tail was repaired by this write, the durable claim
            # can be restored -- but only by recording that a repair happened,
            # never by silently lowering the claim.
            if getattr(self.state, "truncated", None):
                self.state.ledger_records = len(records)
                self.state.ledger_head = self._ledger_tail_hash()
                self.state.truncated = {}
                self.state.recovered.append({
                    "at": now_iso(),
                    "kind": "repaired-truncation",
                    "records": len(records),
                })
                self._save_state()
        return Entry.from_record(rec)

    def _verify_approval_record(self, entry: Entry, record: dict) -> None:
        """Re-verify a host-minted approval record before admitting layer A."""
        if not isinstance(record, dict) or not record:
            raise StoreError("approval record must be a non-empty dict")
        if str(record.get("entry_id") or "") != entry.entry_id:
            raise StoreError("approval record is for a different entry")
        digest = hashlib.sha256(entry.text.encode("utf-8")).hexdigest()
        if record.get("text_sha256") != digest:
            raise StoreError("approval record does not match the entry text")
        if str(record.get("source_event_id") or "") != str(entry.source_event_id or ""):
            raise StoreError("approval record does not match the entry's source event")
        if not approval.verify(self.root, record):
            raise StoreError("approval record does not verify with the host key")

    # -- archive -----------------------------------------------------------
    def _archive_append(self, kind: str, entry: "Entry", reason: str = "") -> None:
        """Append the raw text of a write/approve/revoke to the local archive.

        The archive is not served: it exists so that a human (or a later
        session) can recover the original text behind a typed entry.  Failures
        here must not break the write path, so they are surfaced through
        ``status()["archive_error"]`` instead of raising.
        """
        try:
            self.archive.append(
                kind=kind,
                entry_id=entry.entry_id,
                layer=str(entry.layer),
                text=entry.text,
                source=entry.source or "",
                source_event_id=entry.source_event_id or "",
                authority=entry.authority or "",
                evidence=entry.evidence or "",
                scope=getattr(entry, "scope", "") or "",
                extra={"reason": reason, "tags": list(entry.tags or [])},
            )
            self.state.archive_events = self.archive.count
            self.state.archive_last_sha256 = self.archive.last_sha256
        except Exception as exc:  # pragma: no cover - defensive
            self.state.archive_error = f"{type(exc).__name__}: {exc}"

    def add(self, entry: Entry, approval_record: dict | None = None) -> Entry:
        """Validate, decide, then commit ledger -> layer -> state under one lock.

        The write record carries the *predicted* bytes of the layer file, so a
        crash between ledger and file is repairable, and the bind record carries
        the bytes actually written (v0.7 F03).

        F04: a caller cannot approve its own layer-A claim.  ``approved=True``
        without a host-minted ``approval_record`` is refused, and the record is
        re-verified against the host public key before the entry is admitted.
        """
        if entry.approved and approval_record is None:
            raise StoreError(
                "caller-supplied approval is not accepted: mint a host approval record "
                "(`four-layer-memory approve` / approval.mint) and pass it explicitly")
        if approval_record is not None:
            self._verify_approval_record(entry, approval_record)
        with self._lock():
            self._reload()
            entry.validate()
            entry.at = entry.at or now_iso()
            records = self.ledger_records()
            existing = self._check_idempotent(entry, records)
            if existing is not None:
                return existing
            ids, sources = self.revoked_sets()
            if entry.entry_id in ids or (entry.source_event_id and entry.source_event_id in sources) \
                    or (set(entry.derived_from) & (ids | sources)):
                # F02/R05: new material derived from revoked material must not
                # become loadable just because it was written after the revoke.
                self._write_quarantine(entry, "lineage was revoked")
                self._save_state()
                return entry
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
            expected = self.expected_layers()[str(entry.layer).upper()]
            self._guard_bytes(entry.layer, expected)
            payload = self._append_bullet(expected, entry)
            tx = secrets.token_hex(6)
            event = "approve" if approval_record is not None else "write"
            extra = ({"approver": str((approval_record or {}).get("approver") or "host"),
                      "approval_sha256": _sha(_canonical(approval_record or {})),
                      "approval_record": approval_record}
                     if approval_record is not None else {})
            self._ledger({
                "event": event,
                "tx": tx,
                **entry.to_record(),
                **extra,
                "layer_sha256_expected": _sha(payload),
                "reason": decision.reason,
            })
            # the archive records the decision, so it must survive a crash
            # between the ledger write and the layer materialisation.
            self._archive_append(event, entry, decision.reason)
            self._append_locked(entry, payload)
            self._ledger({
                "event": "bind",
                "tx": tx,
                "entry_id": entry.entry_id,
                "layer": entry.layer,
                "layer_sha256": _sha(payload),
            })
            self._save_state()
            return entry

    def add_many(self, entries: list[Entry],
                 approvals: dict[str, dict] | None = None) -> list[Entry]:
        """Add several entries under one lock and one state write.

        Layer-A entries need a host approval record in ``approvals`` keyed by
        entry id; the same F04 contract as :meth:`add` applies.
        """
        approvals = approvals or {}
        out = []
        with self._lock():
            self._reload()
            for entry in entries:
                record = approvals.get(entry.entry_id)
                if entry.approved and record is None:
                    raise StoreError(
                        "caller-supplied approval is not accepted: provide one host-minted "
                        "approval record per layer-A entry")
                if record is not None:
                    self._verify_approval_record(entry, record)
                entry.validate()
                entry.at = entry.at or now_iso()
                records = self.ledger_records()
                existing = self._check_idempotent(entry, records)
                if existing is not None:
                    out.append(existing)
                    continue
                ids, sources = self.revoked_sets()
                if entry.entry_id in ids or (entry.source_event_id and entry.source_event_id in sources) \
                        or (set(entry.derived_from) & (ids | sources)):
                    self._write_quarantine(entry, "lineage was revoked")
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
                    expected = self.expected_layers()[str(entry.layer).upper()]
                    self._guard_bytes(entry.layer, expected)
                    payload = self._append_bullet(expected, entry)
                    tx = secrets.token_hex(6)
                    event = "approve" if record is not None else "write"
                    extra = ({"approver": str((record or {}).get("approver") or "host"),
                              "approval_sha256": _sha(_canonical(record or {})),
                              "approval_record": record}
                             if record is not None else {})
                    self._ledger({
                        "event": event,
                        "tx": tx,
                        **entry.to_record(),
                        **extra,
                        "layer_sha256_expected": _sha(payload),
                        "reason": decision.reason,
                    })
                    self._archive_append(event, entry, decision.reason)
                    self._append_locked(entry, payload)
                    self._ledger({
                        "event": "bind",
                        "tx": tx,
                        "entry_id": entry.entry_id,
                        "layer": entry.layer,
                        "layer_sha256": _sha(payload),
                    })
                out.append(entry)
            self._save_state()
        return out

    # -- quarantine --------------------------------------------------------
    def _write_quarantine(self, entry: Entry, reason: str) -> None:
        record = {
            **entry.to_record(),
            "reason": reason,
            "quarantined_at": now_iso(),
        }
        block = [
            f"## {entry.entry_id}",
            f"- claimed layer: `{entry.layer}`",
            f"- authority: `{entry.authority}` / evidence: `{entry.evidence}` / lifecycle: `{entry.lifecycle}`",
            f"- source: `{entry.source}` at `{entry.at}`",
            f"- source event: `{entry.source_event_id}`",
            f"- rejected: {reason}",
            "- decision: waiting for the host channel (`four-layer-memory approve` or `quarantine --reject`)",
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
        """Entries in the quarantine file; resolved ones are hidden by default.

        The record deliberately carries **no approval material**: the writer side
        never receives anything it could replay (v0.7 F01).
        """
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
        return []

    def review(self, entry_id: str, accept: bool, token: str = "",
               approver: str = "human") -> dict:
        """Human decision on a quarantined claim.

        Accepting is *not* a write-side action in v0.7: the write side never
        receives approval material, so the only accepting entry point is
        :meth:`approve` (host channel).  ``review`` still rejects.
        """
        if accept:
            raise StoreError(
                "layer A approval is a host action: run `four-layer-memory approve "
                f"{entry_id}` with the host key; the write side never receives approval material"
            )
        with self._lock():
            self._reload()
            match = [e for e in self.quarantine_entries(include_resolved=True)
                     if e["entry_id"] == entry_id]
            if not match:
                raise StoreError(f"no quarantined entry {entry_id}")
            if entry_id in self._resolved_ids():
                raise StoreError(f"already resolved: {entry_id}")
            with self.resolved_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"entry_id": entry_id, "decision": "reject",
                                     "approver": approver, "ts": now_iso()},
                                    ensure_ascii=False) + chr(10))
                fh.flush()
                os.fsync(fh.fileno())
            self._ledger({"event": "review", "entry_id": entry_id, "accept": False,
                          "approver": approver, "declared_token_ignored": bool(token)})
            self._save_state()
        return {"entry_id": entry_id, "decision": "reject", "layer": match[0].get("layer", "")}

    def resolve_quarantine(self, entry_id: str, decision: str = "accepted",
                           approver: str = "host") -> dict:
        """Close a quarantine trail record once the host channel accepted the claim.

        The claim still enters its layer through :meth:`approve`; this only keeps
        the quarantine trail honest.  Without it every *successful*
        ``add --approved`` leaves a phantom "waiting for the host channel" record
        behind, because ``add`` necessarily files the claim in quarantine before
        the host decides (v0.7: the write side never holds approval material).

        Unlike :meth:`review` this is not a rejection: the decision is recorded as
        ``decision=accepted`` and no approval material is stored here either.
        """
        with self._lock():
            self._reload()
            if not any(e["entry_id"] == entry_id
                       for e in self.quarantine_entries(include_resolved=True)):
                raise StoreError(f"no quarantined entry {entry_id}")
            if entry_id in self._resolved_ids():
                return {"entry_id": entry_id, "decision": decision, "skipped": True}
            with self.resolved_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"entry_id": entry_id, "decision": decision,
                                     "approver": approver, "ts": now_iso()},
                                    ensure_ascii=False) + chr(10))
                fh.flush()
                os.fsync(fh.fileno())
            self._ledger({"event": "resolve", "entry_id": entry_id,
                          "decision": decision, "approver": approver})
            self._save_state()
        return {"entry_id": entry_id, "decision": decision, "skipped": False}

    def _repair_approval(self, rec: dict) -> None:
        """Complete a ledger-committed approval whose materialisation was cut short."""
        entry = Entry.from_record(rec)
        layer = str(rec.get("layer", "")).upper()
        path = self.path_for(layer)
        current = path.read_text(encoding="utf-8") if path.exists() else self._header(layer)
        if not (entry.entry_id and f"`id: {entry.entry_id}`" in current):
            payload = self._append_bullet(current, entry)
            self._backup(path)
            self._atomic_write(path, payload)
            current = payload
            key = spec(layer).name
            self.state.layer_counts[key] = self.state.layer_counts.get(key, 0) + 1
        tx = str(rec.get("tx") or "")
        if tx and not self._tx_bound(tx):
            self._ledger({"event": "bind", "tx": tx, "entry_id": entry.entry_id,
                          "layer": layer, "layer_sha256": _sha(current),
                          "recovered": True})

    def approve(self, entry_id: str, approval_record: dict,
                approver: str = "host") -> dict:
        """Host-only acceptance of a quarantined claim into its claimed layer.

        ``approval_record`` must be minted by :func:`approval.mint` with the host
        key; it binds the entry id, the exact text, the source event and the
        scope.  The entry then goes through ``admission.decide`` again, so a
        claim without a verifiable source event cannot reach layer A even when a
        host run ``approve`` (R02).
        """
        with self._lock():
            self._reload()
            prior = self._find_record(entry_id)
            if prior is not None and prior.get("event") == "approve":
                # F03: a crash after the approve record but before bind/resolved
                # is repaired from the ledger instead of duplicating the entry.
                self._repair_approval(prior)
                self._rebuild_resolved()
                self._save_state()
                return {"entry_id": entry_id, "decision": "approve", "layer": str(prior.get("layer") or ""),
                        "idempotent": True}
            match = [e for e in self.quarantine_entries(include_resolved=True)
                     if e["entry_id"] == entry_id]
            if not match:
                raise StoreError(f"no quarantined entry {entry_id}")
            if entry_id in self._resolved_ids():
                raise StoreError(f"already resolved: {entry_id}")
            if not approval.verify(self.root, approval_record):
                raise StoreError("approval record does not verify with the host key")
            if approval_record.get("entry_id") != entry_id:
                raise StoreError("approval record is for a different entry")
            found = match[0]
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
                scope=str(found.get("scope") or ""),
                expires_at=str(found.get("expires_at") or ""),
                entry_id=entry_id,
                approved=True,
            )
            entry.validate()
            if approval_record.get("text_sha256") != hashlib.sha256(entry.text.encode("utf-8")).hexdigest():
                raise StoreError("approval record does not match the entry text")
            if approval_record.get("source_event_id") != entry.source_event_id:
                raise StoreError("approval record does not match the entry's source event")
            ids, sources = self.revoked_sets()
            if (entry.entry_id in ids or (entry.source_event_id and entry.source_event_id in sources)
                    or (set(entry.derived_from) & (ids | sources))):
                raise StoreError("entry or its source event was revoked; approval refused")
            decision = admission.decide(
                entry.layer, entry.authority, entry.evidence,
                source_event_id=entry.source_event_id, approved=True, text=entry.text,
            )
            if not decision.accept:
                raise StoreError(f"candidate is not eligible for {entry.layer}: {decision.reason}")
            expected = self.expected_layers()[str(entry.layer).upper()]
            self._guard_bytes(entry.layer, expected)
            payload = self._append_bullet(expected, entry)
            tx = secrets.token_hex(6)
            self._ledger({
                "event": "approve",
                "tx": tx,
                **entry.to_record(),
                "approver": approver,
                "approval_sha256": _sha(_canonical(approval_record)),
                "approval_record": approval_record,
                "layer_sha256_expected": _sha(payload),
                "reason": decision.reason,
            })
            self._archive_append("approve", entry, decision.reason)
            self._append_locked(entry, payload)
            self._ledger({
                "event": "bind",
                "tx": tx,
                "entry_id": entry.entry_id,
                "layer": entry.layer,
                "layer_sha256": _sha(payload),
            })
            self._rebuild_resolved()
            self._save_state()
        return {"entry_id": entry_id, "decision": "approve", "layer": entry.layer,
                "idempotent": False}

    # -- revocation --------------------------------------------------------
    @staticmethod
    def _closure(records: list[dict]) -> tuple[set[str], set[str]]:
        """Revoked entry ids and source events, closed transitively.

        ``derived_from`` may name either an entry id or a source-event id (F02);
        both are resolved, and a reference to an entry also carries that entry's
        source event.  The walk is a fixed point so chains of any length are
        covered (ancestor -> child -> grandchild).
        """
        ids: set[str] = set()
        sources: set[str] = set()
        entry_recs: dict[str, dict] = {}
        for rec in records:
            if rec.get("event") == "revoke":
                if rec.get("source_event_id"):
                    sources.add(str(rec["source_event_id"]))
                ids.update(str(x) for x in (rec.get("entry_ids") or []))
            elif rec.get("event") in ("write", "approve") and rec.get("entry_id"):
                entry_recs.setdefault(str(rec["entry_id"]), rec)
        event_owner: dict[str, str] = {}
        for eid, rec in entry_recs.items():
            se = str(rec.get("source_event_id") or "")
            if se:
                event_owner.setdefault(se, eid)

        def hit(ref: str, seen: set[str]) -> bool:
            if not ref or ref in seen:
                return False
            seen.add(ref)
            if ref in ids or ref in sources:
                return True
            owner = event_owner.get(ref)
            if owner is not None:
                if owner in ids:
                    return True
                return hit(str(entry_recs[owner].get("source_event_id") or ""), seen)
            if ref in entry_recs:
                rec = entry_recs[ref]
                if str(rec.get("source_event_id") or "") in sources:
                    return True
                for parent in (rec.get("derived_from") or []):
                    if hit(str(parent), seen):
                        return True
            return False

        changed = True
        while changed:
            changed = False
            for eid, rec in entry_recs.items():
                if eid in ids:
                    continue
                source = str(rec.get("source_event_id") or "")
                if source and source in sources:
                    ids.add(eid)
                    changed = True
                    continue
                for ref in (rec.get("derived_from") or []):
                    if hit(str(ref), set()):
                        ids.add(eid)
                        changed = True
                        break
        return ids, sources

    def revoked_sets(self) -> tuple[set[str], set[str]]:
        """Revoked entry ids and source events, as a closure over the ledger."""
        return self._closure(self.ledger_records())

    def revoked_ids(self) -> set[str]:
        return self.revoked_sets()[0]

    def revoke(self, source_event_id: str) -> dict:
        """Revoke every entry derived from a source event, transitively."""
        with self._lock():
            self._reload()
            records = self.ledger_records()
            # Compute the full closure first: derived-from chains must be walked
            # after the tombstone exists, not just matched one level deep (F02).
            tentative = {"event": "revoke", "source_event_id": source_event_id,
                         "entry_ids": []}
            hits, sources = self._closure([*records, tentative])
            entry_by_id = {r.get("entry_id"): r for r in records
                           if r.get("event") in ("write", "approve")}
            for eid in sorted(hits):
                rec = entry_by_id.get(eid)
                if rec is None:
                    continue
                tomb = Entry(
                    layer=Layer(str(rec.get("layer") or "D")),
                    text=str(rec.get("text") or ""),
                    source=str(rec.get("source") or ""),
                    source_event_id=str(rec.get("source_event_id") or ""),
                    entry_id=eid,
                )
                self._archive_append("revoke", tomb, "source event revoked")
            self._ledger(
                {
                    "event": "revoke",
                    "source_event_id": source_event_id,
                    "entry_ids": sorted(hits),
                    "reason": "source event revoked",
                }
            )
            ids, sources = self.revoked_sets()
            self.state.revoked = sorted(ids)
            self.state.layer_counts = self._physical_counts()
            self._save_state()
        return {"source_event_id": source_event_id, "revoked": sorted(hits),
                "revoked_total": sorted(ids), "revoked_sources": sorted(sources)}

    # -- recovery ----------------------------------------------------------
    def _recover_tx(self, rec: dict, records: list[dict] | None = None) -> dict:
        """Repair one write/approve transaction whose bind is missing (F03)."""
        entry = Entry.from_record(rec)
        layer = str(rec.get("layer", "")).upper()
        # the decision log must cover recovered transactions as well; the event
        # is marked reconstructed so an auditor can tell it apart.
        if entry.entry_id and entry.entry_id not in set(self.archive.entry_ids()):
            self._archive_append("reconstructed", entry, "recovered after crash")
        path = self.path_for(layer)
        current = path.read_text(encoding="utf-8") if path.exists() else self._header(layer)
        if entry.entry_id and f"`id: {entry.entry_id}`" in current:
            payload = current
        else:
            payload = self._append_bullet(current, entry)
            self._backup(path)
            self._atomic_write(path, payload)
        bind = self._ledger({
            "event": "bind",
            "tx": str(rec.get("tx") or ""),
            "entry_id": entry.entry_id,
            "layer": layer,
            "layer_sha256": _sha(payload),
            "recovered": True,
        })
        return bind

    def recover(self) -> dict:
        """Replay unbound transactions and recompute durable state from the ledger.

        F01: when the ledger is shorter than the durable claim, **nothing** is
        repaired and nothing is served; the store stays blocked until the
        operator restores the ledger.  F03: every half-committed write/approve
        is completed from the ledger, and the resolved cache is rebuilt.
        """
        with self._lock():
            self._reload()
            # _reload() just replaced the in-memory state; the truncation flag
            # must be re-derived from the durable claim, never inherited.
            records = self.ledger_records()
            blocked = self._detect_truncation(records)
            if blocked:
                self.state.truncated = dict(blocked)
                self._save_state()
                return {"blocked": True, "error": str(blocked.get("reason")),
                        "recovered": [], "revoked": sorted(self.state.revoked or []),
                        "counts": dict(self.state.layer_counts)}
            repaired: list[dict] = []
            for rec in self.ledger_records():
                if rec.get("event") not in ("write", "approve") or not rec.get("tx"):
                    continue
                tx = str(rec["tx"])
                if self._tx_bound(tx):
                    continue
                self._recover_tx(rec)
                repaired.append({"tx": tx, "entry_id": str(rec.get("entry_id") or ""),
                                 "layer": str(rec.get("layer") or "")})
            resolved = self._rebuild_resolved()
            ids, sources = self.revoked_sets()
            self.state.revoked = sorted(ids)
            self.state.layer_counts = self._physical_counts()
            self.state.ledger_records = len(self.ledger_records())
            self.state.ledger_head = self._ledger_tail_hash()
            self.state.store_version = STORE_FORMAT
            if repaired:
                self.state.recovered = (list(self.state.recovered or []) + repaired)[-50:]
            self._save_state()
        self._reload()
        return {"blocked": False, "recovered": repaired, "resolved": resolved,
                "revoked": sorted(ids), "revoked_sources": sorted(sources),
                "counts": dict(self.state.layer_counts)}

    # -- compaction --------------------------------------------------------
    def compact(self, apply: bool = False, layers: list[str] | None = None) -> dict:
        """Physically drop revoked bullets from layer files (ledger preserved).

        Revocation is a ledger tombstone: the block stays in the layer file for
        replay while the loader skips it (F02).  That is correct but grows files
        forever.  ``compact`` rewrites a layer file to header + live bullets and
        records the dropped ids in a ``compact`` ledger event; ``expected_layers``
        skips those ids, so the file stays a pure function of the ledger.

        Dry-run by default: call with ``apply=True`` to rewrite.  Only entries
        that are already in the revocation closure are dropped -- live memory is
        never touched by this operation.
        """
        wanted = {str(x).upper() for x in layers} if layers else {l.value for l in Layer}
        with self._lock():
            self._reload()
            ids, sources = self.revoked_sets()
            records = self.ledger_records()
            already = self._dropped_ids(records)
            report: dict = {"applied": bool(apply), "layers": {}, "dropped": []}
            for layer in Layer:
                if layer.value not in wanted:
                    continue
                skip = set(already.get(layer.value, set()))
                expected = self._layer_from_ledger(layer, records, skip)
                path = self.path_for(layer)
                current = path.read_text(encoding="utf-8") if path.exists() else self._header(layer)
                blocks, _unknown = self.parse_layer_text(current)
                drop = [e.entry_id for e, _raw in blocks
                        if e.entry_id in ids
                        or (e.source_event_id and e.source_event_id in sources)
                        or (e.derived_from and set(e.derived_from) & (ids | sources))]
                kept = len(blocks) - len(drop)
                info: dict = {"dropped": len(drop), "kept": kept,
                              "before_sha256": _sha(current)}
                if drop:
                    new_text = self._layer_from_ledger(layer, records, skip | set(drop))
                    info["after_sha256"] = _sha(new_text)
                    info["dropped_ids"] = drop
                    report["dropped"].extend(drop)
                    if apply:
                        self._guard_bytes(layer.value, expected)
                        self._backup(path)
                        self._atomic_write(path, new_text)
                        self._ledger({
                            "event": "compact", "tx": secrets.token_hex(6),
                            "layer": layer.value, "dropped_ids": drop,
                            "before_sha256": _sha(current), "after_sha256": _sha(new_text),
                            "reason": "revoked cleanup",
                        })
                        self.state.layer_counts[spec(layer).name] = kept
                report["layers"][spec(layer).target] = info
            if apply:
                self._save_state()
            return report

    # -- loading -----------------------------------------------------------
    def _is_expired(self, entry: Entry) -> bool:
        return _is_expired(entry.expires_at) if entry.expires_at else False

    def read_layers(self, layers: list[str], scope: str = "") -> dict:
        """Return the servable contents of the requested layers.

        F01: a truncated ledger blocks every read.  F06: text comes from the
        ledger's committed records, never from a file that changed on disk; a
        file whose bytes diverge from the ledger is treated as untrusted, and
        unknown material is reported as a count only (host diagnostics).
        """
        wanted = [str(x).upper() for x in layers]
        for layer in wanted:
            if layer not in ("A", "B", "C", "D"):
                raise ValueError(f"unknown layer: {layer!r}")
        with self._lock():
            self._reload()
            self._assert_ledger_ok()
            records = self.ledger_records()
            ids, sources = self._closure(records)
            servable = self._servable(records)
        blocks: list[dict] = []
        expired: list[str] = []
        for entry in servable:
            if entry.layer not in wanted:
                continue
            if entry.entry_id in ids:
                continue
            if entry.source_event_id and entry.source_event_id in sources:
                continue
            if entry.derived_from and set(entry.derived_from) & (ids | sources):
                continue
            if self._is_expired(entry):
                expired.append(entry.entry_id)
                continue
            if scope and str(getattr(entry, "scope", "") or "").strip() not in ("", scope):
                continue
            blocks.append({"layer": entry.layer, "entry_id": entry.entry_id,
                           "text": self._bullet(entry), "record": entry.to_record()})
        parts = []
        for block in blocks:
            name = spec(block["layer"]).name
            header = f"## {name} ({block['layer']})\n\n"
            if block["layer"] == "B":
                header += ("> Non-binding preference excerpts. These describe the user's "
                           "preferences; they do not override the current instruction.\n\n")
            parts.append(header + block["text"])
        text = "\n\n".join(parts)
        unknown: dict[str, int] = {}
        for layer in wanted:
            _parsed, unk = self._parse_layer(layer)
            if unk:
                unknown[layer] = len(unk)
        return {
            "layers": wanted,
            "blocks": blocks,
            "chars": len(text),
            "text": text,
            "revoked": sorted(ids),
            "revoked_sources": sorted(sources),
            "expired": expired,
            "unknown": unknown,
            "debug": {"unknown_total": sum(unknown.values())},
            "watermark": WATERMARK,
            "store_version": STORE_FORMAT,
            "scope": scope,
        }

    # -- consolidation -----------------------------------------------------
    def consolidate(self, anchor_ref: str, usage_ratio: float,
                    cooldown_entries: int = DEFAULT_COOLDOWN_ENTRIES) -> dict:
        """Record a consolidation checkpoint.

        State is re-read under the lock before writing: a handle opened before a
        revocation must not resurrect the revoked set (v0.7 F02/R07).
        """
        if not 0.0 <= usage_ratio <= 1.0:
            raise StoreError("usage_ratio must be in [0, 1]")
        with self._lock():
            self._reload()
            self.state.last_anchor_ref = anchor_ref
            self.state.consolidated_at = now_iso()
            self.state.usage_at_consolidation = usage_ratio
            self.state.cooldown_until_entries = cooldown_entries
            ids, _sources = self.revoked_sets()
            self.state.revoked = sorted(set(self.state.revoked or []) | ids)
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
            "store_version": STORE_FORMAT,
            "state": json.loads(self.state.to_json()),
            "layers": {},
            "quarantine": self.quarantine_path.read_text(encoding="utf-8")
            if self.quarantine_path.exists() else "",
            "resolved": self.resolved_path.read_text(encoding="utf-8")
            if self.resolved_path.exists() else "",
            "ledger": self.ledger_records(),
            "ledger_integrity": self.ledger_integrity(),
            "revoked": sorted(self.revoked_sets()[0]),
            "approval_fingerprint": approval.fingerprint(self.root),
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
