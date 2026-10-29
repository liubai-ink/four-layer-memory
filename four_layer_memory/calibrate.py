# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Calibrate watermark thresholds from observed context-usage samples.

The defaults in ``triggers.Watermark`` are a guess. This module turns a log of
real sessions into a starting point: it reads ``usage`` samples (one per turn)
and places the four thresholds at quantiles of the observed distribution, with
hard bounds so a skewed log cannot push the policy into nonsense.

Design notes
------------
* Only the *fold* and *emergency* thresholds are host constraints (the host
  folds at 45 % and stops at 95 % by default); calibration must not exceed them.
* ``consolidate_at`` is placed below ``fold_at`` by at least 3 points, because a
  consolidation that arrives after folding cannot recover what folding dropped.
* If the log has too few samples (< 20) the function refuses rather than
  pretending to calibrate.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict

from .triggers import Watermark


class CalibrationError(RuntimeError):
    pass


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise CalibrationError("no samples")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def calibrate(usages: list[float], host_fold: float = 0.45,
              host_emergency: float = 0.95) -> dict:
    """Return calibrated thresholds plus the evidence used."""
    values = sorted(float(u) for u in usages if 0.0 <= float(u) <= 1.0)
    if len(values) < 20:
        raise CalibrationError(f"need >= 20 usage samples, got {len(values)}")

    check_every_3 = min(max(_quantile(values, 0.25), 0.10), host_fold - 0.15)
    check_every_turn = min(max(_quantile(values, 0.40), check_every_3 + 0.03), host_fold - 0.10)
    consolidate_at = min(max(_quantile(values, 0.60), check_every_turn + 0.02), host_fold - 0.05)
    fold_at = host_fold
    emergency_at = host_emergency

    ordered = [check_every_3, check_every_turn, consolidate_at, fold_at, emergency_at]
    if any(b <= a for a, b in zip(ordered, ordered[1:])):
        raise CalibrationError("calibration produced non-monotone thresholds")

    return {
        "samples": len(values),
        "observed": {
            "p25": round(_quantile(values, 0.25), 4),
            "p50": round(_quantile(values, 0.50), 4),
            "p75": round(_quantile(values, 0.75), 4),
            "p95": round(_quantile(values, 0.95), 4),
            "max": round(values[-1], 4),
        },
        "watermark": asdict(Watermark(
            check_every_3=round(check_every_3, 4),
            check_every_turn=round(check_every_turn, 4),
            consolidate_at=round(consolidate_at, 4),
            fold_at=fold_at,
            emergency_at=emergency_at,
        )),
        "rationale": (
            "check at p25/p40, consolidate at p60 but at least 5 points below the "
            "host fold threshold; fold and emergency stay at host limits because "
            "they are constraints, not preferences."
        ),
    }


def load_samples(path: str | pathlib.Path) -> list[float]:
    """Read ``{"usage": 0.0-1.0}`` per line, or a JSON array of numbers."""
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return [float(x) for x in json.loads(text)]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out.append(float(rec.get("usage", rec.get("context_usage"))))
    return out


def write_thresholds(path: str | pathlib.Path, wm: dict) -> pathlib.Path:
    out = pathlib.Path(path)
    out.write_text(json.dumps(wm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
