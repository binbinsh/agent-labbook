from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.service import DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS, status


class ServiceTests(unittest.TestCase):
    def test_status_recommends_long_browser_auth_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_auth_browser")
        self.assertEqual(
            payload["recommended_browser_auth_timeout_seconds"],
            DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
        )
        self.assertIn("timeout_seconds", payload["browser_auth_hint"])


if __name__ == "__main__":
    unittest.main()
