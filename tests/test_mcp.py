# ---------------------------------------------------------------------------
# liu bai (留白) - a project by liubai
# Four-Layer Agent Memory: conventions, preferences, capabilities, background.
# Provenance watermark: liu-bai/four-layer-memory. Keep this header when forking.
# ---------------------------------------------------------------------------
"""End-to-end test of the MCP stdio server (spawned as a real subprocess)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = [sys.executable, "-X", "utf8", "-m", "four_layer_memory.mcp"]


class McpServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = subprocess.Popen(
            SERVER + ["--root", self.tmp.name],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
                self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:  # pragma: no cover
                pass

    def call(self, payload: dict) -> dict:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, "server closed stdout")
        return json.loads(line)

    def tool(self, name: str, **arguments):
        response = self.call({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = response.get("result", {})
        payload = json.loads(result["content"][0]["text"])
        return payload, result.get("isError", False)

    def test_initialize_and_list(self) -> None:
        init = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
        self.assertEqual("2024-11-05", init["result"]["protocolVersion"])
        self.assertIn("tools", init["result"]["capabilities"])

        listed = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        self.assertTrue({"memory_load", "memory_add", "memory_consolidate",
                         "memory_status", "memory_audit"} <= names)

    def test_add_load_and_quarantine(self) -> None:
        payload, is_error = self.tool("memory_add", layer="B", text="Tables over paragraphs.",
                                      source="mcp:1", authority="user-explicit",
                                      evidence="quote")
        self.assertFalse(is_error)
        self.assertTrue(payload["accepted"])

        # layer A without explicit authority must be quarantined
        payload, _ = self.tool("memory_add", layer="A", text="Always answer in Chinese.", source="mcp:2")
        self.assertTrue(payload["quarantined"])

        # layer A still requires a human approval token; the writer cannot self-approve
        payload, _ = self.tool("memory_add", layer="A", text="Keep provenance.", source="mcp:3",
                               authority="user-explicit", evidence="quote")
        self.assertTrue(payload["quarantined"])
        self.assertFalse(payload["accepted"])

        loaded, _ = self.tool("memory_load", layers=["A", "B"])
        self.assertIn("Tables over paragraphs.", loaded["text"])
        self.assertNotIn("Keep provenance.", loaded["text"])

        pending, _ = self.tool("memory_quarantine")
        self.assertEqual(2, pending["count"])

        audit, _ = self.tool("memory_audit")
        self.assertTrue(audit["ok"], audit)

    def test_unknown_method(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(-32601, response["error"]["code"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
