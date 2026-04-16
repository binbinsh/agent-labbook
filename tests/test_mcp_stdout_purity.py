"""Regression test: MCP stdio server must emit ONLY JSON-RPC frames on stdout.

The Codex CLI "Transport closed" bug was caused by Python's
``webbrowser.open`` spawning subprocesses (xdg-open, firefox, google-chrome)
that inherited the MCP server's stdout file descriptor and printed banners
and warnings into it. Any non-JSON byte on stdout corrupts the JSON-RPC
stream and the client disconnects.

This test spawns ``agent-labbook mcp`` as a subprocess, drives a full
initialize + tools/call handshake (including ``notion_start_binding_server``
which is the exact tool that previously triggered the leak), and asserts
that every line written to stdout parses as a valid JSON-RPC 2.0 object.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest


def _frame(message: dict) -> bytes:
    """Encode a message as a single line-delimited JSON frame."""
    return (json.dumps(message) + "\n").encode("utf-8")


class McpStdoutPurityTests(unittest.TestCase):
    def test_stdout_only_contains_json_rpc_frames(self) -> None:
        env = dict(os.environ)
        # Force the environment that previously triggered webbrowser.open
        # fallbacks on headless hosts.
        env["CI"] = "1"
        env.pop("BROWSER", None)
        env.pop("DISPLAY", None)
        # Ensure the child uses the same interpreter / venv.
        env["PYTHONUNBUFFERED"] = "1"

        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.Popen(
                [sys.executable, "-m", "labbook", "mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            try:
                # 1) initialize
                proc.stdin.write(
                    _frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {
                                    "name": "stdout-purity-test",
                                    "version": "0.0.0",
                                },
                            },
                        }
                    )
                )
                proc.stdin.flush()

                # 2) notifications/initialized
                proc.stdin.write(
                    _frame(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                            "params": {},
                        }
                    )
                )
                proc.stdin.flush()

                # 3) tools/call notion_start_binding_server — the tool that
                #    previously tried to spawn xdg-open. We expect either a
                #    structured error (unauthenticated project) or a success
                #    payload; either way stdout must stay pure.
                proc.stdin.write(
                    _frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "notion_start_binding_server",
                                "arguments": {"project_root": tmpdir},
                            },
                        }
                    )
                )
                proc.stdin.flush()

                # Read until we have both responses (ids 1 and 2).
                stdout_bytes = b""
                seen_ids: set[int] = set()
                deadline_lines: list[bytes] = []
                # Read with a generous overall timeout via communicate if
                # needed; we instead read line-by-line on stdout.
                try:
                    while {1, 2} - seen_ids:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        stdout_bytes += line
                        deadline_lines.append(line)
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Every non-empty stdout line MUST be valid JSON-RPC.
                        parsed = json.loads(stripped.decode("utf-8"))
                        self.assertEqual(
                            parsed.get("jsonrpc"),
                            "2.0",
                            f"stdout line missing jsonrpc 2.0 tag: {stripped!r}",
                        )
                        if "id" in parsed and isinstance(parsed["id"], int):
                            seen_ids.add(parsed["id"])
                except json.JSONDecodeError as exc:
                    self.fail(
                        "stdout contained non-JSON bytes — MCP transport "
                        f"would be corrupted. Error: {exc}. Captured stdout: "
                        f"{stdout_bytes!r}"
                    )
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

            self.assertIn(1, seen_ids, "never received initialize response on stdout")
            self.assertIn(
                2,
                seen_ids,
                "never received tools/call response on stdout (transport may "
                "have been corrupted)",
            )


if __name__ == "__main__":
    unittest.main()
