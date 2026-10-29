# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""A minimal MCP server so any MCP-capable host can use this memory.

The server speaks JSON-RPC 2.0 over stdio (one JSON message per line), which is
the transport used by Claude Desktop, Cursor and most MCP clients.  It depends on
nothing but the Python standard library and this package.

Point it at a memory root with ``--root`` or the ``FOUR_LAYER_MEMORY_ROOT``
environment variable; the directory is created, keyed and initialised on first
use.  On the host side, expose it like this (Claude Desktop style):

    {
      "mcpServers": {
        "four-layer-memory": {
          "command": "four-layer-memory-mcp",
          "args": ["--root", "/path/to/memory"]
        }
      }
    }

Tools (all return JSON text):
    memory_load        load A and B (optionally C or D) into the context
    memory_add         write one entry; layer A must earn its authority
    memory_consolidate record a consolidation checkpoint (the watermark)
    memory_status      counts, watermark, signing algorithm
    memory_audit       structural / content / authorization / ledger checks
    memory_quarantine  list entries waiting for a human decision
    memory_review      accept or reject one quarantined entry
    memory_handoff     read or update the cursor used to resume a new session
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import signing
from .audit import audit_store
from .handoff import Handoff
from .layers import LAYERS, Layer, spec
from .store import DEFAULT_COOLDOWN_ENTRIES, Entry, MemoryStore, StoreError
from .watermark import WATERMARK

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "four-layer-memory"
SERVER_VERSION = "0.7.8"


def _root() -> Path:
    return Path(os.environ.get("FOUR_LAYER_MEMORY_ROOT", ".four-layer-memory"))


def _store() -> MemoryStore:
    """Open the store without minting state.

    A signing key is created only by an explicit ``init``/``keygen`` call:
    writing authenticated records on behalf of a root the caller has not
    initialised would make earlier unsigned history look tampered with.
    """
    return MemoryStore(_root())


def tool_load(args: dict) -> dict:
    layers = args.get("layers") or ["A", "B"]
    if isinstance(layers, str):
        layers = [layers]
    layers = [str(x).upper() for x in layers]
    for layer in layers:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer: {layer!r}")
    scope = str(args.get("scope") or "")
    loaded = _store().read_layers(layers, scope=scope)
    return {
        "layers": loaded["layers"],
        "chars": loaded["chars"],
        "text": loaded["text"],
        "revoked": loaded["revoked"],
        "revoked_sources": loaded["revoked_sources"],
        "expired": loaded["expired"],
        # R052: a claim the caller may not use, and a claim replaced by a newer
        # one, are both reported -- silently omitting them would look like the
        # store never had them.
        "failed": loaded.get("failed", []),
        "superseded": loaded.get("superseded", []),
        # F06: unknown material is reported as a count on the diagnostics
        # channel, never as text in a successful tool result.
        "unknown": loaded.get("unknown", {}),
        "diagnostics": loaded.get("debug", {}),
        "scope": loaded.get("scope", ""),
        "store_version": loaded["store_version"],
        "watermark": WATERMARK,
    }


def tool_add(args: dict) -> dict:
    entry = Entry(
        layer=str(args.get("layer", "")).upper(),
        text=str(args.get("text", "")),
        source=str(args.get("source", "")),
        authority=str(args.get("authority", "inferred")),
        evidence=str(args.get("evidence", "summary")),
        lifecycle=str(args.get("lifecycle", "standing")),
        source_event_id=str(args.get("source_event_id", "")),
        scope=str(args.get("scope", "") or ""),
        expires_at=str(args.get("expires_at", "") or ""),
        kind=str(args.get("kind", "fact") or "fact"),
        approved=False,  # approval is never granted by the writer
    )
    store = _store()
    added = store.add(entry)
    pending = [x for x in store.quarantine_entries() if x.get("entry_id") == added.entry_id]
    detail = "stored"
    if pending:
        detail = (f"quarantined: {pending[0].get('reason', 'needs human approval')}. "
                  "A human must approve it on the host channel "
                  f"(`four-layer-memory approve {added.entry_id}`); "
                  "nothing the write side received can be replayed as approval.")
    return {
        "layer": added.layer,
        "entry_id": added.entry_id,
        "accepted": not pending,
        "quarantined": bool(pending),
        "detail": detail,
        "scope": getattr(added, "scope", ""),
        "expires_at": getattr(added, "expires_at", ""),
        "lifecycle": getattr(added, "lifecycle", ""),
        "kind": getattr(added, "kind", "fact"),
        "watermark": WATERMARK,
    }


def tool_consolidate(args: dict) -> dict:
    store = _store()
    status = store.consolidate(
        str(args.get("anchor_ref", "")),
        float(args.get("usage_ratio", 0.45)),
        int(args.get("cooldown_entries", DEFAULT_COOLDOWN_ENTRIES)),
    )
    status["watermark"] = WATERMARK
    return status


def tool_status(_: dict) -> dict:
    store = _store()
    status = store.status()
    status["signing"] = store.signing_info()
    status["watermark"] = WATERMARK
    return status


def tool_audit(_: dict) -> dict:
    return audit_store(_root())


def tool_quarantine(_: dict) -> dict:
    store = _store()
    pending = store.quarantine_entries()
    return {"pending": pending, "count": len(pending), "watermark": WATERMARK}


def tool_review(args: dict) -> dict:
    store = _store()
    if bool(args.get("accept", False)):
        # F01: the write side cannot approve.  Only the host channel can.
        raise StoreError(
            "layer A approval is a host action: acceptance is not offered over MCP; "
            "the human runs `four-layer-memory approve <entry_id>`; MCP can only reject"
        )
    result = store.review(str(args.get("entry_id", "")), False)
    result["watermark"] = WATERMARK
    return result


def tool_recall(args: dict) -> dict:
    store = _store()
    hits = store.search(
        str(args.get("query", "")),
        layer=(args.get("layer") or None),
        include_revoked=bool(args.get("include_revoked", False)),
        include_failed=bool(args.get("include_failed", False)),
        limit=int(args.get("limit", 10)),
    )
    return {"query": args.get("query", ""), "count": len(hits),
            "entries": hits, "watermark": WATERMARK}


def tool_record_outcome(args: dict) -> dict:
    """Score an experience entry by how it actually performed (R052)."""
    raw_ids = args.get("entry_ids") or args.get("entry_id") or []
    if isinstance(raw_ids, str):
        raw_ids = [x for x in raw_ids.split(",") if x.strip()]
    ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    if not ids:
        raise StoreError("memory_record_outcome needs at least one entry id")
    ok = bool(args.get("ok", not bool(args.get("failed", False))))
    report = _store().record_outcome(ids, ok=ok,
                                     reason=str(args.get("reason", "") or ""))
    report["watermark"] = WATERMARK
    return report


def tool_archive_search(args: dict) -> dict:
    """Keyword search over the local raw archive (never injected)."""
    store = _store()
    hits = store.archive.search(
        str(args.get("query", "")),
        limit=int(args.get("limit", 10)),
        include_revoked=bool(args.get("include_revoked", True)),
    )
    return {"query": args.get("query", ""), "count": len(hits),
            "entries": hits, "watermark": WATERMARK}


def tool_archive_verify(args: dict) -> dict:
    """Verify the archive hash chain (tamper evidence) and basic counts."""
    store = _store()
    report = store.archive.verify()
    return {"ok": report["ok"], "events": report["count"],
            "problems": report["problems"], "stats": store.archive.stats(),
            "watermark": WATERMARK}


def tool_handoff(args: dict) -> dict:
    handoff = Handoff(_root() / "handoff.json")
    after_ref = args.get("mark_consolidated")
    if after_ref:
        handoff.mark_consolidated(str(after_ref))
    return {"state": handoff.state.__dict__, "watermark": WATERMARK}


TOOLS = [
    {
        "name": "memory_load",
        "description": "Load memory layers into the conversation. Defaults to A (conventions) and B (preferences); add C or D only when relevant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "layers": {"type": "array", "items": {"type": "string", "enum": ["A", "B", "C", "D"]}},
                "scope": {"type": "string", "description": "only serve entries with this scope (empty scope entries are always served)"},
            },
        },
    },
    {
        "name": "memory_add",
        "description": "Write one memory entry. Layer A (conventions) requires authority=user-explicit, evidence=quote and a source_event_id, otherwise the entry is quarantined for host approval. scope and expires_at are persisted; expired entries are not loaded. The tool result never contains approval material.",
        "inputSchema": {
            "type": "object",
            "required": ["layer", "text"],
            "properties": {
                "layer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                "text": {"type": "string"},
                "source": {"type": "string", "description": "message id, file path or URL"},
                "authority": {"type": "string", "enum": ["user-explicit", "user-implied", "observed", "inferred", "model-generated", "none"]},
                "evidence": {"type": "string", "enum": ["quote", "paraphrase", "summary", "inference", "none"]},
                "lifecycle": {"type": "string"},
                "source_event_id": {"type": "string"},
                "scope": {"type": "string", "description": "optional scope label; lifecycle enforcement is host-side"},
                "expires_at": {"type": "string", "description": "optional ISO date; expired entries are not loaded"},
                "kind": {"type": "string", "enum": ["fact", "experience"], "description": "experience entries can be scored later with memory_record_outcome; facts cannot"},
            },
        },
    },
    {
        "name": "memory_consolidate",
        "description": "Record a consolidation checkpoint after folding new material into the layers.",
        "inputSchema": {
            "type": "object",
            "required": ["anchor_ref"],
            "properties": {
                "anchor_ref": {"type": "string"},
                "usage_ratio": {"type": "number"},
                "cooldown_entries": {"type": "integer"},
            },
        },
    },
    {"name": "memory_status", "description": "Counts, watermark and signing algorithm.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "memory_audit", "description": "Run structural, content, authorization and ledger checks.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "memory_quarantine", "description": "List entries waiting for a human decision. The host approves or rejects; MCP can only reject.", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "memory_review",
        "description": "Reject one quarantined entry by id. Acceptance is host-only (`four-layer-memory approve`); this tool never accepts.",
        "inputSchema": {
            "type": "object",
            "required": ["entry_id"],
            "properties": {"entry_id": {"type": "string"}, "accept": {"type": "boolean", "description": "must be false; acceptance is a host action"}},
        },
    },
    {
        "name": "memory_record_outcome",
        "description": "Score experience entries by how they performed. A failed experience is no longer served (R052): following it is how one bad decision spreads. Facts cannot be scored -- they are decided at the write path.",
        "inputSchema": {
            "type": "object",
            "required": ["entry_ids"],
            "properties": {
                "entry_ids": {"type": "array", "items": {"type": "string"}},
                "ok": {"type": "boolean"},
                "failed": {"type": "boolean", "description": "convenience alias for ok=false"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "memory_handoff",
        "description": "Read the cross-session cursor, or mark consolidation at a message reference.",
        "inputSchema": {
            "type": "object",
            "properties": {"mark_consolidated": {"type": "string"}},
        },
    },
    {
        "name": "memory_archive_search",
        "description": "Search the local append-only raw archive by keyword. The archive is never injected into the conversation; use it to recover the original text behind a typed entry. Returns revoked flags.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "include_revoked": {"type": "boolean"},
            },
        },
    },
    {
        "name": "memory_archive_verify",
        "description": "Verify the raw archive hash chain and report event counts. A broken chain means a line was deleted or rewritten.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_recall",
        "description": "Typed recall: search stored entries and return them with authority, evidence, lifecycle and revocation state. Returns entries (not raw transcript chunks), so the caller can tell authorised knowledge from passing remarks.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "layer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                "limit": {"type": "integer"},
                "include_revoked": {"type": "boolean", "description": "also return entries retracted by a revoke event"},
                "include_failed": {"type": "boolean", "description": "audit only: also return experience entries judged failed"},
            },
        },
    },
]

HANDLERS = {
    "memory_load": tool_load,
    "memory_add": tool_add,
    "memory_consolidate": tool_consolidate,
    "memory_status": tool_status,
    "memory_audit": tool_audit,
    "memory_quarantine": tool_quarantine,
    "memory_review": tool_review,
    "memory_handoff": tool_handoff,
    "memory_archive_search": tool_archive_search,
    "memory_archive_verify": tool_archive_verify,
    "memory_recall": tool_recall,
    "memory_record_outcome": tool_record_outcome,
}


def _reply(msg_id, result=None, error=None) -> dict:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        return _reply(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION, "watermark": WATERMARK},
        })
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _reply(msg_id, {"pong": True})
    if method == "tools/list":
        return _reply(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _reply(msg_id, error={"code": -32602, "message": f"unknown tool: {name}"})
        try:
            payload = handler(args)
            return _reply(msg_id, {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False,
            })
        except (ValueError, StoreError, KeyError, FileNotFoundError) as exc:
            return _reply(msg_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
    return _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps(_reply(None, error={"code": -32700, "message": str(exc)})) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="four-layer-memory-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="memory root (default: $FOUR_LAYER_MEMORY_ROOT)")
    args = parser.parse_args(argv)
    root = args.root or _root()
    os.environ["FOUR_LAYER_MEMORY_ROOT"] = str(root)
    MemoryStore(root)
    if not signing.has_key(root):
        signing.keygen(root)
    return serve()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
