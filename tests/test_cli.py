from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.cli import main


class CliTests(unittest.TestCase):
    def test_print_mcp_config_defaults_to_pypi_package(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["print-mcp-config", "--server-name", "labbook"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload,
            {
                "mcpServers": {
                    "labbook": {
                        "command": "uvx",
                        "args": ["agent-labbook", "mcp"],
                    }
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
