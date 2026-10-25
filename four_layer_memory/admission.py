# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Write-time admission rules (v0.6 trust boundary).

The point of this module is to keep four things apart that prose tends to merge:
  kind       -- what a memory is about (layer A/B/C/D)
  authority  -- who had the standing to make it binding
  evidence   -- how it is grounded (quoted, paraphrased, summarised, inferred)
  lifecycle  -- how long it is meant to survive

Layer A and B are *loaded into the prompt*; C and D are not.  So admission is
not symmetric:

  A  binding conventions: user-explicit + quote + immutable source event id +
     recorded human approval.  Nothing else enters A.
  B  preferences: user-explicit or user-implied, quoted or paraphrased.  They
     are loaded as *non-binding excerpts*, which is why they do not need the
     approval ceremony -- but a model-generated guess cannot become a
     preference, and a summary cannot either.
  C/D non-binding stores: accepted, still subject to the sensitive-content gate.

Sensitive content (medical, identity, financial) is quarantined in every layer,
because "background" is not a licence to persist a diagnosis.

Semantic shape never grants authority.  A sentence can look exactly like a rule
and still be a hypothesis about a rule; it belongs in quarantine until the user
confirms it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

BINDING_LAYER = "A"
PREFERENCE_LAYER = "B"

# patterns that mark content a "background" store has no business keeping
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(hiv|hepatitis|herpes|cancer|diagnos|prescription|dosage|"
               r"depress|anxiety disorder|therapy|surgery|medical (note|record|history)|"
               r"patient record|clinic|hospital|symptom|allerg)"),
    re.compile(r"(病历|诊断|处方|用药|手术|精神科|抑郁|焦虑症|化验单|体检报告)"),
    re.compile(r"(?i)(passport|id card|social security|bank account|iban|credit card)"),
    re.compile(r"(身份证|银行卡|护照号|学号|工号|社保号)"),
    re.compile(r"(?i)(salary|net worth|debt|loan balance|income of)"),
    re.compile(r"(工资|年薪|存款|负债|贷款余额)"),
)


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


def is_sensitive(text: str) -> bool:
    return bool(text) and any(p.search(text) for p in SENSITIVE_PATTERNS)


def decide(layer: str, authority=None, evidence=None, *, source_event_id: str = "",
           approved: bool = False, text: str = "") -> Decision:
    """Decide whether an entry may be written to its claimed layer."""
    layer, auth, ev, _ = normalize(layer, authority, evidence)
    if is_sensitive(text):
        return Decision(False, "sensitive content requires human approval", "quarantine")
    if layer == BINDING_LAYER:
        if auth is not Authority.USER_EXPLICIT:
            return Decision(False, f"layer A requires user-explicit authority; got {auth.value}", "quarantine")
        if ev is not Evidence.QUOTE:
            return Decision(False, f"layer A requires quoted evidence; got {ev.value}", "quarantine")
        if not source_event_id:
            return Decision(False, "layer A requires the immutable source event id", "quarantine")
        if not approved:
            return Decision(False, "layer A requires recorded human approval", "quarantine")
        return Decision(True, "explicit, quoted, approved user instruction", "A")
    if layer == PREFERENCE_LAYER:
        if auth not in (Authority.USER_EXPLICIT, Authority.USER_IMPLIED):
            return Decision(False, f"layer B holds user preferences; got authority {auth.value}", "quarantine")
        if ev not in (Evidence.QUOTE, Evidence.PARAPHRASE):
            return Decision(False, f"layer B needs a quote or paraphrase; got {ev.value}", "quarantine")
        return Decision(True, "user preference (loaded as a non-binding excerpt)", "B")
    return Decision(True, f"layer {layer}: non-binding store", layer)
