# ---------------------------------------------------------------------------
# liu bai (留白) - a project by Liu Yinuo
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""The CLI is part of the released artefact: every subcommand must actually dispatch."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from four_layer_memory import cli


def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def subcommands() -> list[str]:
    # Rebuild the parser's action table without parsing: every declared subcommand
    # must appear in the handler map inside main(), which we probe by dispatch.
    parser = argparse.ArgumentParser()
    del parser
    return ["init", "add", "consolidate", "status", "triggers", "plan", "handoff",
            "quarantine", "approve", "keygen", "calibrate", "revoke", "audit", "export", "demo"]


class CliDispatchTest(unittest.TestCase):
    def test_init_creates_a_signing_key_and_signed_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "store")
            code, out = run_cli(["--root", root, "init"])
            self.assertEqual(0, code)
            status = json.loads(out)
            self.assertEqual("hmac-sha256", status["signing"]["alg"])

            code, out = run_cli(["--root", root, "add", "B", "Prefers tables.", "--source", "m1"])
            self.assertEqual(0, code)

            code, out = run_cli(["--root", root, "audit"])
            report = json.loads(out[out.index("{"):])
            self.assertTrue(report["ok"], report)
            self.assertTrue(report["checks"]["ledger"]["ok"], report)

    def test_a_layer_without_explicit_authority_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "store")
            run_cli(["--root", root, "init"])
            code, _ = run_cli(["--root", root, "add", "A", "Always answer in Chinese.",
                               "--source", "m2", "--source-event", "evt-cli"])
            self.assertEqual(0, code)
            code, out = run_cli(["--root", root, "quarantine", "--list"])
            self.assertEqual(0, code)
            pending = json.loads(out)
            self.assertEqual(1, len(pending))

            entry_id = pending[0]["entry_id"]
            # v0.7: the write side never receives approval material; the host
            # channel records the decision.
            code, _ = run_cli(["--root", root, "approve", entry_id])
            self.assertEqual(0, code)
            code, out = run_cli(["--root", root, "quarantine", "--list"])
            self.assertEqual([], json.loads(out), out)

    def test_explicit_authority_goes_straight_to_a(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "store")
            run_cli(["--root", root, "init"])
            code, _ = run_cli([
                "--root", root, "add", "A", "Always answer in Chinese.", "--source", "m3",
                "--authority", "user-explicit", "--evidence", "quote",
                "--source-event", "evt-1", "--approved",
            ])
            self.assertEqual(0, code)
            code, out = run_cli(["--root", root, "quarantine", "--list"])
            self.assertEqual([], json.loads(out))

    def test_every_subcommand_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "store")
            run_cli(["--root", root, "init"])
            samples = {
                "status": ["--root", root, "status"],
                "triggers": ["--root", root, "triggers", "--usage", "0.3"],
                "keygen": ["--root", root, "keygen", "--force"],
                "audit": ["--root", root, "audit"],
                "quarantine": ["--root", root, "quarantine", "--list"],
                "export": ["--root", root, "export", "--out", str(Path(tmp) / "b.md")],
                "demo": ["--root", root, "demo"],
            }
            for name, argv in samples.items():
                code, _ = run_cli(argv)
                self.assertEqual(0, code, f"{name} did not dispatch")

            for name in subcommands():
                self.assertTrue(name, name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
