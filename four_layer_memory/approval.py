# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Host-held approval records for layer A (v0.7).

In v0.6 the store minted an ``approval_token`` when an entry was quarantined and
returned it to the *same* agent that had just written the entry.  That made the
approval check decorative: the writer could read the token from its own tool
result and replay it into ``review``.  The fix is to separate the two channels:

* the agent (MCP/CLI write side) can only *request* a decision; it never sees
  approval material;
* the host runs ``four-layer-memory approve <entry_id>``; that command mints a
  signed approval record with a key that never enters a tool result, and only
  then does the entry move into layer A.

An approval record binds four things, so it cannot be lifted from one entry and
pasted onto another:

    {format: 2, scope: "layer:A", entry_id, text_sha256,
     source_event_id, approver, minted_at, sig}

``scope`` says what the approval is *for* (today only layer A), ``text_sha256``
pins the wording, ``source_event_id`` pins the event the claim came from, and
``format`` lets a future version reject records it does not understand.  The
signature is HMAC-SHA256 over the canonical body with a key file that only the
host channel creates (``approval.key``), so an agent that can only call tools
never holds it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

APPROVAL_KEY = "approval.key"
APPROVAL_FORMAT = 2
LAYER_A_SCOPE = "layer:A"


class ApprovalError(RuntimeError):
    pass


def _canonical(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "sig"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def key_path(root: str | Path) -> Path:
    return Path(root) / APPROVAL_KEY


def has_key(root: str | Path) -> bool:
    return key_path(root).exists()


def ensure_key(root: str | Path) -> Path:
    """Create the host approval key on first use (host channel only)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    p = key_path(root)
    if not p.exists():
        p.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:  # pragma: no cover - ACL-managed filesystems
            pass
    return p


def fingerprint(root: str | Path) -> str:
    """Short, non-secret identifier for the approval key (for status output)."""
    p = key_path(root)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_text(encoding="utf-8").strip().encode("utf-8")).hexdigest()[:16]


def mint(root: str | Path, *, entry_id: str, text: str, source_event_id: str,
         approver: str = "host") -> dict:
    """Mint a signed approval record.  Only the host channel should call this."""
    key = ensure_key(root).read_text(encoding="utf-8").strip().encode("utf-8")
    record = {
        "format": APPROVAL_FORMAT,
        "scope": LAYER_A_SCOPE,
        "entry_id": entry_id,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_event_id": source_event_id,
        "approver": approver,
        "minted_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    record["sig"] = hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()
    return record


def verify(root: str | Path, record: dict | None) -> bool:
    """Check the signature and the record format; content binding is checked by the store."""
    if not isinstance(record, dict):
        return False
    if record.get("format") != APPROVAL_FORMAT or record.get("scope") != LAYER_A_SCOPE:
        return False
    p = key_path(root)
    if not p.exists():
        return False
    key = p.read_text(encoding="utf-8").strip().encode("utf-8")
    want = hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, str(record.get("sig", "")))
