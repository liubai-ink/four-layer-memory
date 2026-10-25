# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Four-layer memory for long-horizon LLM agents.

Layers
------
A  conventions  - stable rules of engagement ("how we work together")
B  preferences  - execution-level defaults (format, order, tool habits)
C  capabilities - tools, assets, procedures worth remembering
D  background   - ordinary facts, anecdotes, loose context

The package keeps the agent's *working memory* small: it stores a pointer
state (where the last consolidation ended, how full the context is) and
keeps the long tail on disk, organised by layer and retrievable on demand.
"""

from .layers import Layer, LAYERS, LayerSpec
from .store import MemoryStore, Entry, StoreError
from .triggers import Watermark, Action, decide
from .audit import audit_store
from .watermark import WATERMARK, stamp

__all__ = [
    "Layer", "LAYERS", "LayerSpec", "MemoryStore", "Entry", "StoreError",
    "Watermark", "Action", "decide", "audit_store", "WATERMARK", "stamp",
]
__version__ = "0.1.0"
