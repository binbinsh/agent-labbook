from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.cli import main


class CliTests(unittest.TestCase):
    def test_print_mcp_config_with_explicit_ref(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "print-mcp-config",
                    "--server-name",
                    "labbook",
                    "--ref",
                    "v0.11.0",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload,
            {
                "mcpServers": {
                    "labbook": {
                        "command": "uvx",
                        "args": [
                            "--from",
                            "git+https://github.com/binbinsh/agent-labbook@v0.11.0",
                            "agent-labbook",
                            "mcp",
                        ],
                    }
                }
            },
        )

    def test_print_mcp_config_defaults_to_current_version_tag(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), patch("labbook.cli.__version__", "0.11.0"):
            exit_code = main(["print-mcp-config", "--server-name", "labbook"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload,
            {
                "mcpServers": {
                    "labbook": {
                        "command": "uvx",
                        "args": [
                            "--from",
                            "git+https://github.com/binbinsh/agent-labbook@v0.11.0",
                            "agent-labbook",
                            "mcp",
                        ],
                    }
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
