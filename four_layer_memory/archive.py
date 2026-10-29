# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Append-only raw archive with a hash-chained event log.

The archive is deliberately *not* part of the prompt: it is a local,
append-only record of every write/approve/revoke, kept so that a later
session (or a human) can recover the original text behind a typed entry.
Retrieval is opt-in via keyword search; nothing here is injected by
``memory_load``.

Layout (under ``<root>/archive/``)::

    events.jsonl   one JSON object per line, ``prev_sha256`` chained
    index.sqlite   optional FTS5 index over ``text`` (rebuilt on demand)

The chain makes tampering detectable: deleting or rewriting any line
breaks ``prev_sha256`` for every later line.  The archive is *not* a
substitute for the ledger: the ledger authorises what is *served*, the
archive preserves what was *written*.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARCHIVE_VERSION = 1
GENESIS_PREV = "0" * 64


# ---------------------------------------------------------------------------
# helpers


def _canonical(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class ArchiveError(RuntimeError):
    pass


class Archive:
    """Append-only, hash-chained raw archive."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.dir = self.root / "archive"
        self.events_path = self.dir / "events.jsonl"
        self.db_path = self.dir / "index.sqlite"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- write ---------------------------------------------------------
    def _tail(self) -> Tuple[str, int]:
        """Return (last sha256, line count) without loading the whole file."""
        if not self.events_path.exists():
            return GENESIS_PREV, 0
        prev = GENESIS_PREV
        n = 0
        with self.events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                obj = json.loads(line)
                prev = obj.get("sha256", prev)
        return prev, n

    def append(self, *, kind: str, entry_id: str, layer: str = "",
               text: str = "", source: str = "", source_event_id: str = "",
               authority: str = "", evidence: str = "", scope: str = "",
               extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        prev_sha, count = self._tail()
        record: Dict[str, Any] = {
            "v": ARCHIVE_VERSION,
            "at": _now(),
            "seq": count + 1,
            "kind": kind,
            "entry_id": entry_id,
            "layer": layer,
            "text": text,
            "text_sha256": _sha256(text.encode("utf-8")) if text else "",
            "source": source,
            "source_event_id": source_event_id,
            "authority": authority,
            "evidence": evidence,
            "scope": scope,
            "prev_sha256": prev_sha,
        }
        if extra:
            record.update(extra)
        record["sha256"] = _sha256(_canonical(record))
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False,
                                sort_keys=True) + "\n")
        self._index_one(record)
        return record

    # -- verify --------------------------------------------------------
    def verify(self) -> Dict[str, Any]:
        problems: List[str] = []
        count = 0
        prev = GENESIS_PREV
        if not self.events_path.exists():
            return {"ok": True, "count": 0, "problems": []}
        with self.events_path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception as exc:  # pragma: no cover - corrupt line
                    problems.append(f"line {lineno}: unparseable ({exc})")
                    continue
                count += 1
                if obj.get("prev_sha256") != prev:
                    problems.append(
                        f"line {lineno}: prev_sha256 mismatch (chain broken)")
                claimed = obj.pop("sha256", None)
                actual = _sha256(_canonical(obj))
                obj["sha256"] = claimed
                if claimed != actual:
                    problems.append(f"line {lineno}: sha256 mismatch (tampered)")
                prev = claimed or actual
        return {"ok": not problems, "count": count, "problems": problems}

    @property
    def last_sha256(self) -> str:
        return self._tail()[0]

    @property
    def count(self) -> int:
        return self._tail()[1]

    # -- search --------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " seq INTEGER PRIMARY KEY, at TEXT, kind TEXT, entry_id TEXT,"
            " layer TEXT, text TEXT, source_event_id TEXT)"
        )
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
                " text, seq UNINDEXED, entry_id UNINDEXED, layer UNINDEXED,"
                " tokenize='unicode61')"
            )
            self._has_fts = True
        except sqlite3.OperationalError:
            self._has_fts = False
        return con

    def _index_one(self, record: Dict[str, Any]) -> None:
        try:
            con = self._connect()
            with con:
                con.execute(
                    "INSERT OR REPLACE INTO events(seq, at, kind, entry_id,"
                    " layer, text, source_event_id) VALUES (?,?,?,?,?,?,?)",
                    (record["seq"], record["at"], record["kind"],
                     record["entry_id"], record["layer"], record["text"],
                     record["source_event_id"]),
                )
                if record["text"]:
                    if self._has_fts:
                        con.execute("DELETE FROM events_fts WHERE seq=?",
                                    (record["seq"],))
                        con.execute(
                            "INSERT INTO events_fts(text, seq, entry_id, layer)"
                            " VALUES (?,?,?,?)",
                            (record["text"], record["seq"],
                             record["entry_id"], record["layer"]),
                        )
                    else:
                        pass
            con.close()
        except sqlite3.Error:
            # Index is a convenience; the JSONL log is the source of truth.
            pass

    def rebuild_index(self) -> int:
        if self.db_path.exists():
            self.db_path.unlink()
        self._connect().close()
        n = 0
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    self._index_one(json.loads(line))
                    n += 1
        return n

    def search(self, query: str, limit: int = 20,
               include_revoked: bool = True) -> List[Dict[str, Any]]:
        if not query.strip():
            return []
        con = self._connect()
        rows: List[Tuple] = []
        if getattr(self, "_has_fts", False):
            try:
                rows = con.execute(
                    "SELECT e.seq, e.at, e.kind, e.entry_id, e.layer, e.text,"
                    " e.source_event_id FROM events_fts f"
                    " JOIN events e ON e.seq = f.seq"
                    " WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = f"%{query}%"
            rows = con.execute(
                "SELECT seq, at, kind, entry_id, layer, text, source_event_id"
                " FROM events WHERE text LIKE ? ORDER BY seq DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        con.close()
        revoked = self._revoked_ids()
        out = []
        for seq, at, kind, entry_id, layer, text, sev in rows:
            out.append({
                "seq": seq, "at": at, "kind": kind, "entry_id": entry_id,
                "layer": layer, "text": text, "source_event_id": sev,
                "revoked": entry_id in revoked,
            })
        if not include_revoked:
            out = [r for r in out if not r["revoked"]]
        return out

    def _revoked_ids(self) -> set:
        ids = set()
        if not self.events_path.exists():
            return ids
        with self.events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("kind") == "revoke":
                    ids.add(obj.get("entry_id", ""))
        return ids

    def entry_ids(self) -> set:
        """Entry ids that appear as capture (write/approve) or revoke events."""
        ids = set()
        if not self.events_path.exists():
            return ids
        with self.events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("kind") in ("write", "approve", "revoke"):
                    ids.add(obj.get("entry_id", ""))
        return ids

    def stats(self) -> Dict[str, Any]:
        kinds: Dict[str, int] = {}
        total = 0
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    total += 1
                    kinds[obj.get("kind", "?")] = kinds.get(obj.get("kind", "?"), 0) + 1
        return {"events": total, "kinds": kinds,
                "last_sha256": self.last_sha256,
                "bytes": self.events_path.stat().st_size
                if self.events_path.exists() else 0}
