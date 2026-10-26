# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Context-watermark driven trigger policy.

The agent does not need to re-read the whole history to know whether the
moment is safe for another turn.  It needs three numbers:

    usage      - fraction of the context window currently occupied
    turns      - turns since the last consolidation
    entries    - entries added since the last consolidation

and a monotone policy that says what to do.  Thresholds below are a useful
default, not a law of nature; they are configurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    NONE = "none"
    CHECK = "check"            # sample the usage every few turns
    CONSOLIDATE = "consolidate"  # write the delta into layer files now
    FOLD = "fold"              # let the host fold/compress (mechanical)
    EMERGENCY = "emergency"    # stop new work; consolidate before continuing


@dataclass(frozen=True)
class Watermark:
    check_every_3: float = 0.30
    check_every_turn: float = 0.35
    consolidate_at: float = 0.38
    fold_at: float = 0.45
    emergency_at: float = 0.95


def decide(usage: float, turns_since: int = 0, entries_since: int = 0,
           cooldown_entries: int = 0, wm: Watermark | None = None,
           allow_fold: bool = False) -> Action:
    """Return the action that should be taken *now*.

    ``entries_since`` counts writes that have not yet been folded into the
    layers.  Folding is allowed only when at least one entry is pending: a
    mechanical fold that drops the only copy of a just-written entry is exactly
    the failure this policy exists to prevent (v0.7 F01; the paper's "at least
    one pending entry" now matches the code).
    """
    wm = wm or Watermark()
    if usage >= wm.emergency_at:
        return Action.EMERGENCY
    if usage >= wm.fold_at:
        if allow_fold and entries_since > 0:
            return Action.FOLD
        return Action.CONSOLIDATE
    if entries_since >= cooldown_entries and usage >= wm.consolidate_at:
        return Action.CONSOLIDATE
    if usage >= wm.check_every_turn:
        return Action.CHECK
    if usage >= wm.check_every_3 and turns_since >= 3:
        return Action.CHECK
    return Action.NONE
