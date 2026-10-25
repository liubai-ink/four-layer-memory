# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Consolidation helpers.

Two jobs:

1. ``plan()``   - classify candidate memories into layers (deterministic rules;
                  an LLM may replace ``classify`` for better accuracy).
2. ``render()`` - render a compact, source-referenced digest that can be
                  written into the layer files.

Design constraint: every stored item keeps a source reference.  A memory
without provenance is a rumour.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layers import Layer, spec
from .store import Entry

_RULES: list[tuple[Layer, tuple[str, ...]]] = [
    (Layer.CONVENTION, ("always", "never", "must", "policy", "rule", "习惯", "约定", "必须", "不许")),
    (Layer.PREFERENCE, ("prefer", "format", "order", "style", "tone", "偏好", "格式", "顺序", "语气")),
    (Layer.CAPABILITY, ("script", "tool", "command", "pipeline", "api", "工具", "脚本", "入口", "命令")),
]


def classify(text: str) -> Layer:
    low = text.lower()
    for layer, keys in _RULES:
        if any(k in low for k in keys):
            return layer
    return Layer.BACKGROUND


@dataclass
class Candidate:
    text: str
    source: str
    layer: Layer | None = None
    tags: tuple[str, ...] = ()


def plan(candidates: list[Candidate]) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {spec(Layer(x)).name: [] for x in Layer}
    for c in candidates:
        layer = c.layer or classify(c.text)
        entry = Entry(layer=layer.value, text=c.text, source=c.source, tags=list(c.tags))
        entry.validate()
        out[spec(layer).name].append(entry)
    return out


def render(entries: list[Entry], heading: str) -> str:
    lines = [f"## {heading}", ""]
    for e in entries:
        lines.append(f"- {e.text}")
        lines.append(f"  `source: {e.source}` `at: {e.at}`")
    lines.append("")
    return "\n".join(lines)
