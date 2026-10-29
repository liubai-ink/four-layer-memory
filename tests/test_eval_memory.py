# liu bai (留白): evaluation harness tests. Provenance watermark: liu bai.
"""P1-7: the three write-path metrics must all be green on a healthy tree."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_memory.py"


class EvalMemoryCase(unittest.TestCase):
    def test_script_is_green(self) -> None:
        out = Path(tempfile.mkdtemp()) / "report.json"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--quiet", "--json", str(out)],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(report["ok"], report["metrics"])
        metrics = report["metrics"]
        self.assertEqual(metrics["write_gate"]["precision"], 1.0)
        self.assertTrue(metrics["write_gate"]["clean_all_admitted"])
        self.assertEqual(metrics["revoke"]["residual_rate"], 0.0)
        self.assertTrue(metrics["revoke"]["survivor_still_served"])
        self.assertEqual(metrics["tamper"]["detect_rate"], 1.0)
        self.assertEqual(len(metrics["tamper"]["cases"]), 5)

    def test_report_shape_is_stable(self) -> None:
        out = Path(tempfile.mkdtemp()) / "report.json"
        subprocess.run([sys.executable, str(SCRIPT), "--quiet", "--json", str(out)],
                       check=True, cwd=str(ROOT), timeout=300)
        report = json.loads(out.read_text(encoding="utf-8"))
        for key in ("ok", "thresholds", "checks", "metrics", "note"):
            self.assertIn(key, report)


if __name__ == "__main__":
    unittest.main()
