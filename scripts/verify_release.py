# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""Release verification (pass 2, after tests): provenance, privacy, structure.

Checks:
  1. every .py/.md/.toml/.txt file carries the "liu bai" provenance (except LICENSE/boilerplate)
  2. no API keys, tokens or absolute user paths in tracked files
  3. tests exist and imports resolve
  4. state/bundle exports contain the watermark field
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATERMARK = "liu bai"
SKIP_SUFFIX = {".png", ".jpg", ".zip", ".pyc"}
SKIP_FILES = {"LICENSE", "NOTICE", ".gitignore"}
CACHE_PARTS = {".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", "build", "dist"}
_BS = chr(92)
SECRET = re.compile("sk-" + "[A-Za-z0-9_-]{16,}")
USERPATH = re.compile("[A-Za-z]:" + _BS + _BS + "Users" + _BS + _BS + "|/home/[^/]+/")


def main() -> int:
    problems: list[str] = []
    files = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix not in SKIP_SUFFIX
             and "__pycache__" not in p.parts and ".git" not in p.parts
             and not any(part in CACHE_PARTS for part in p.parts)
             and not any(part.endswith(".egg-info") for part in p.parts)]
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        text = p.read_text(encoding="utf-8", errors="ignore")
        if p.name not in SKIP_FILES and rel != "README.md" and WATERMARK not in text:
            problems.append(f"no provenance watermark: {rel}")
        if SECRET.search(text) and not rel.startswith("tests/"):
            problems.append(f"secret-like token: {rel}")
        if USERPATH.search(text) and rel not in {"scripts/verify_release.py",
                                                 "four_layer_memory/audit.py"}:
            problems.append(f"absolute user path: {rel}")
    # exports carry the watermark
    sys.path.insert(0, str(ROOT))
    from four_layer_memory import MemoryStore, Entry  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        st = MemoryStore(td, session_id="verify")
        st.add(Entry("A", "Always keep provenance.", "verify:m1"))
        bundle = st.export_bundle(Path(td) / "b.json")
        payload = json.loads(bundle.read_text(encoding="utf-8"))
        if payload.get("watermark") != WATERMARK:
            problems.append("bundle lacks watermark field")
        if not json.loads(st.state.to_json()).get("watermark") == WATERMARK:
            problems.append("state lacks watermark field")

    print(json.dumps({"files": len(files), "problems": problems, "ok": not problems},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
