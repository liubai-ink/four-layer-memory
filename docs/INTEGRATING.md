<!--
  liu bai (留白) - a project by Liu Yinuo
  Four-Layer Agent Memory: conventions, preferences, capabilities, background.
  Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
-->
# Integrating with your agent

Three ways in, from lightest to most integrated. All of them use the same store
on disk, so you can start with the CLI and add MCP later without migrating
anything.

## 0. Install

```bash
pip install -e .            # from a clone
# or, once published: pip install four-layer-memory
```

Python 3.10+, no dependencies.

## 1. CLI (nothing to code)

```bash
four-layer-memory --root ./memory init
four-layer-memory --root ./memory add B "Tables over paragraphs." --source chat:42
four-layer-memory --root ./memory add A "Always answer in Chinese." --source chat:43 \
    --authority user-explicit --evidence quote
four-layer-memory --root ./memory status
four-layer-memory --root ./memory quarantine --list
four-layer-memory --root ./memory audit
```

Use this to inspect and repair a store by hand. `init` also creates the signing
key, so the ledger is signed from the first entry.

## 2. Python API (a few lines in your host)

```python
from four_layer_memory import Entry, MemoryStore
from four_layer_memory.layers import spec

store = MemoryStore("./memory")
store.add(Entry(layer="B", text="Tables over paragraphs.", source="chat:42"))

for layer in ("A", "B"):                       # what you put in every prompt
    path = store.root / spec(layer).target
    print(path.read_text(encoding="utf-8"))
```

The store is a directory of markdown files plus a JSON state and a signed
ledger; you can read it with any editor.

## 3. MCP server (host support, no host code)

`four-layer-memory-mcp` speaks MCP over stdio. Point your host at it:

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "four-layer-memory": {
      "command": "four-layer-memory-mcp",
      "args": ["--root", "/absolute/path/to/memory"]
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`) or any other MCP client: same shape.

**From a clone without installing**: `command: "python"`, `args:
["-m", "four_layer_memory.mcp", "--root", "/path/to/memory"]`.

Then the model can call eight tools:

| Tool | What it does |
|---|---|
| `memory_load` | load A and B (optionally C or D) into the conversation |
| `memory_add` | write one entry; layer A is quarantined unless it carries explicit authority |
| `memory_consolidate` | record a consolidation checkpoint and update the cursor |
| `memory_status` | counts, watermark, signing algorithm |
| `memory_audit` | structural / content / authorization / ledger checks |
| `memory_quarantine` | list entries waiting for a human decision |
| `memory_review` | accept or reject one quarantined entry |
| `memory_handoff` | read the cross-session cursor, or mark consolidation at a reference |

## The host's side of the contract

The store cannot decide what goes into a prompt; the host does. Minimum viable
integration:

1. **Each turn** — call `memory_load` with `["A", "B"]` and put the text in the
   system prompt. It is a few hundred characters, not a transcript.
2. **When the user sets a rule** — call `memory_add` with layer A, the exact
   quote as `text`, `authority="user-explicit"`, `evidence="quote"`; if the tool
   answers `quarantined: true`, ask the user once and then `memory_review`.
3. **When context usage crosses your threshold** — call `memory_consolidate`
   with the reference of the last folded message; next session starts there.
4. **When a new session starts** — call `memory_handoff` and replay only the
   delta after the cursor.
5. **When a task needs a tool or a corpus** — ask for layer C explicitly instead
   of loading everything.

`prompts/LOADING.md` is the human-readable version of these rules; paste it (or
its summary) into your agent's instructions so the model knows the protocol.

## Verifying before you trust it

```bash
python -m unittest discover -s tests -q     # 46 tests
python scripts/verify_release.py            # provenance, privacy, structure
four-layer-memory --root ./memory audit     # structural / content / auth / ledger
```

The audit returns `{"ok": true}` only when all four checks pass. The ledger is a
hash chain, signed with an HMAC key created by `init`; `four-layer-memory
keygen --alg ed25519` upgrades to a public-key signature that a third party can
verify with `fingerprint`.
