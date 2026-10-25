# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Zhu (刘竹) and Shen Bai (沈白)
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Provenance watermark helpers.

The watermark is deliberately boring: a short constant, stamped into exported
artifacts (state files, bundles, reports).  It is not a license enforcement
mechanism; it makes provenance visible and greppable.
"""

WATERMARK = "liu bai"

HEADER = (
    "# ---------------------------------------------------------------------------\n"
    "# liu bai (留白) - Four-Layer Agent Memory\n"
    "# Authors: Liu Zhu (刘竹) and Shen Bai (沈白)\n"
    "# Provenance watermark: liu bai\n"
    "# ---------------------------------------------------------------------------\n"
)


def stamp(text: str) -> str:
    """Return text with the watermark header prepended once."""
    if WATERMARK in text[:400]:
        return text
    return HEADER + text
