# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Write-time admission rules.

The point of this module is to keep four things apart that prose tends to merge:
  kind       -- what a memory is about (layer A/B/C/D)
  authority  -- who had the standing to make it binding
  evidence   -- how it is grounded (quoted, paraphrased, summarised, inferred)
  lifecycle  -- how long it is meant to survive

Semantic shape never grants authority. A sentence can look exactly like a rule
and still be a hypothesis about a rule; it belongs in quarantine until the user
confirms it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

BINDING_LAYER = "A"


class Authority(str, Enum):
    USER_EXPLICIT = "user-explicit"      # the user said it, in this channel
    USER_IMPLIED = "user-implied"        # reasonable inference from what the user said
    OBSERVED = "observed"                # the agent saw it happen (paths, tool output)
    INFERRED = "inferred"                # the agent guessed from patterns
    MODEL_GENERATED = "model-generated"  # the agent wrote it about itself


class Evidence(str, Enum):
    QUOTE = "quote"                # verbatim from the source event
    PARAPHRASE = "paraphrase"      # reworded, same speaker
    SUMMARY = "summary"            # compressed account
    INFERENCE = "inference"        # not stated by anyone


class Lifecycle(str, Enum):
    STANDING = "standing"     # until revoked
    PROJECT = "project-scoped"
    EXPIRING = "expiring"


VALID_LAYERS = ("A", "B", "C", "D")


@dataclass(frozen=True)
class Decision:
    accept: bool
    reason: str
    destination: str  # layer label, or "quarantine"


def _coerce(value, enum_cls, default):
    if value is None or value == "":
        return default
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().lower())
    except ValueError as exc:  # pragma: no cover - exercised via tests
        allowed = ", ".join(m.value for m in enum_cls)
        raise ValueError(f"invalid {enum_cls.__name__}: {value!r} (allowed: {allowed})") from exc


def normalize(layer: str, authority=None, evidence=None, lifecycle=None):
    """Return (layer, Authority, Evidence, Lifecycle) with defaults filled in."""
    layer = (layer or "").strip().upper()
    if layer not in VALID_LAYERS:
        raise ValueError(f"unknown layer: {layer!r} (allowed: {', '.join(VALID_LAYERS)})")
    # Defaults describe the common, benign path: the user said it, verbatim.
    return (
        layer,
        _coerce(authority, Authority, Authority.USER_EXPLICIT),
        _coerce(evidence, Evidence, Evidence.QUOTE),
        _coerce(lifecycle, Lifecycle, Lifecycle.STANDING),
    )


def decide(layer: str, authority=None, evidence=None) -> Decision:
    """Decide whether an entry may be written to its claimed layer."""
    layer, auth, ev, _ = normalize(layer, authority, evidence)
    if layer != BINDING_LAYER:
        return Decision(True, f"layer {layer}: deterministic loading not required", layer)
    if auth is not Authority.USER_EXPLICIT:
        return Decision(
            False,
            f"layer A requires user-explicit authority; got {auth.value}",
            "quarantine",
        )
    if ev is not Evidence.QUOTE:
        return Decision(
            False,
            f"layer A requires quoted evidence; got {ev.value}",
            "quarantine",
        )
    return Decision(True, "explicit, quoted user instruction", "A")
