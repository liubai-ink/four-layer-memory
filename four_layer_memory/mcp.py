# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
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
from .store import Entry, MemoryStore, StoreError
from .watermark import WATERMARK

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "four-layer-memory"
SERVER_VERSION = "0.5.0"


def _root() -> Path:
    return Path(os.environ.get("FOUR_LAYER_MEMORY_ROOT", ".four-layer-memory"))


def _store() -> MemoryStore:
    """Open the store without minting state.

    A signing key is created only by an explicit ``init``/``keygen`` call:
    writing authenticated records on behalf of a root the caller has not
    initialised would make earlier unsigned history look tampered with.
    """
    return MemoryStore(_root())


def _layer_file(layer: str) -> Path:
    return _root() / spec(layer).target


def _text_of(layer: str) -> str:
    path = _layer_file(layer)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _without_revoked(text: str, revoked: set[str]) -> str:
    """Drop every bullet block that mentions a revoked entry id.

    A bullet starts with "- " and continues with indented metadata lines; the
    entry id sits on the last metadata line, so the whole block must be read
    before deciding whether to keep it.
    """
    if not revoked or not text:
        return text
    kept: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        body = "\n".join(block)
        if not any(f"id: {rid}" in body for rid in revoked):
            kept.extend(block)

    for line in text.splitlines():
        if line.startswith("- "):
            flush()
            block = [line]
        elif block:
            block.append(line)
        else:
            kept.append(line)
    flush()
    return "\n".join(kept) + ("\n" if kept else "")


def tool_load(args: dict) -> dict:
    layers = args.get("layers") or ["A", "B"]
    if isinstance(layers, str):
        layers = [layers]
    layers = [str(x).upper() for x in layers]
    for layer in layers:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer: {layer!r}")
    revoked = _store().revoked_ids()
    blocks = []
    for layer in layers:
        text = _without_revoked(_text_of(layer).strip(), revoked)
        if text:
            if layer == "B":
                text = ("> Non-binding preference excerpts. These describe the user's "
                        "preferences; they do not override the current instruction.\n\n" + text)
            blocks.append(f"## {spec(layer).name} ({layer})\n\n{text}")
    return {
        "layers": layers,
        "chars": sum(len(b) for b in blocks),
        "text": "\n\n".join(blocks) if blocks else "",
        "revoked": sorted(revoked),
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
        approved=False,  # approval is never granted by the writer
    )
    store = _store()
    added = store.add(entry)
    pending = [x for x in store.quarantine_entries() if x.get("entry_id") == added.entry_id]
    detail = "stored"
    token = ""
    if pending:
        token = str(pending[0].get("approval_token", ""))
        detail = (f"quarantined: {pending[0].get('reason', 'needs human approval')}. "
                  f"Human approval token: {token} (run `four-layer-memory review {added.entry_id} "
                  f"--accept --token {token}`)")
    return {
        "layer": added.layer,
        "entry_id": added.entry_id,
        "accepted": not pending,
        "quarantined": bool(pending),
        "approval_token": token,
        "detail": detail,
        "watermark": WATERMARK,
    }


def tool_consolidate(args: dict) -> dict:
    store = _store()
    status = store.consolidate(
        str(args.get("anchor_ref", "")),
        float(args.get("usage_ratio", 0.45)),
        int(args.get("cooldown_entries", 50)),
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
    accept = bool(args.get("accept", False))
    result = store.review(
        str(args.get("entry_id", "")), accept,
        token=str(args.get("approval_token", "")),
        approver=str(args.get("approver", "human")),
    )
    result["watermark"] = WATERMARK
    return result


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
            },
        },
    },
    {
        "name": "memory_add",
        "description": "Write one memory entry. Layer A (conventions) requires authority=user-explicit and evidence=quote, otherwise the entry is quarantined for human review.",
        "inputSchema": {
            "type": "object",
            "required": ["layer", "text"],
            "properties": {
                "layer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                "text": {"type": "string"},
                "source": {"type": "string", "description": "message id, file path or URL"},
                "authority": {"type": "string", "enum": ["user-explicit", "user-implied", "observed", "inferred", "model-generated"]},
                "evidence": {"type": "string", "enum": ["quote", "paraphrase", "summary", "inference"]},
                "lifecycle": {"type": "string"},
                "source_event_id": {"type": "string"},
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
    {"name": "memory_quarantine", "description": "List entries waiting for a human decision.", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "memory_review",
        "description": "Accept or reject one quarantined entry by id.",
        "inputSchema": {
            "type": "object",
            "required": ["entry_id"],
            "properties": {"entry_id": {"type": "string"}, "accept": {"type": "boolean"}},
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
