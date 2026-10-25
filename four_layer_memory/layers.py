# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Layer definitions (A/B/C/D)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Layer(str, Enum):
    CONVENTION = "A"   # habits and agreements: what we always do
    PREFERENCE = "B"   # execution defaults: formats, ordering, tool habits
    CAPABILITY = "C"   # tools, assets, procedures: what we know how to do
    BACKGROUND = "D"   # ordinary facts and anecdotes: the long tail


@dataclass(frozen=True)
class LayerSpec:
    layer: Layer
    name: str
    question: str
    target: str
    max_chars: int
    examples: tuple[str, ...] = field(default_factory=tuple)


LAYERS: dict[Layer, LayerSpec] = {
    Layer.CONVENTION: LayerSpec(
        Layer.CONVENTION, "conventions",
        "What has to stay true every time we work together?",
        "conventions.md",
        200,
        ("Always report the conclusion before the evidence.",
         "Never touch production data without an explicit go-ahead."),
    ),
    Layer.PREFERENCE: LayerSpec(
        Layer.PREFERENCE, "preferences",
        "What execution defaults does the human expect?",
        "preferences/work.md",
        300,
        ("Tables over paragraphs for comparisons.",
         "Ask before long-running jobs at peak hours."),
    ),
    Layer.CAPABILITY: LayerSpec(
        Layer.CAPABILITY, "capabilities",
        "Which tools, assets or procedures are worth remembering?",
        "capabilities/index.md",
        400,
        ("The corpus lives in corpus.db; query it with corpus_query.py.",
         "Rendering pipeline: source -> markdown -> PDF via LibreOffice."),
    ),
    Layer.BACKGROUND: LayerSpec(
        Layer.BACKGROUND, "background",
        "What ordinary context is worth keeping, without bloating working memory?",
        "background/archive.md",
        300,
        ("The project started on 2026-10-19.",
         "The human prefers to work late when the house is quiet."),
    ),
}


def spec(layer: Layer | str) -> LayerSpec:
    if isinstance(layer, str):
        key = layer.strip().upper()
        aliases = {"A": Layer.CONVENTION, "B": Layer.PREFERENCE,
                   "C": Layer.CAPABILITY, "D": Layer.BACKGROUND}
        if key not in aliases:
            raise KeyError(f"unknown layer: {layer!r}")
        layer = aliases[key]
    return LAYERS[layer]
