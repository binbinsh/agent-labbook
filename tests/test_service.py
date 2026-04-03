from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
import json


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.service import (
    DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    MIN_BROWSER_AUTH_PAGE_LIMIT,
    normalize_browser_auth_page_limit,
    status,
)


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

    def test_page_limit_is_clamped_to_minimum(self) -> None:
        self.assertEqual(normalize_browser_auth_page_limit(None), DEFAULT_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(normalize_browser_auth_page_limit(50), MIN_BROWSER_AUTH_PAGE_LIMIT)

    def test_status_clears_stale_pending_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".labbook"
            state_dir.mkdir(parents=True, exist_ok=True)
            pending_auth_path = state_dir / "pending-auth.json"
            pending_auth_path.write_text(
                json.dumps(
                    {
                        "mode": "local_browser",
                        "session_id": "stale-session",
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "timeout_seconds": 30,
                    }
                ),
                encoding="utf-8",
            )

            payload = status(tmpdir)

        self.assertIsNone(payload["pending_auth"])
        self.assertTrue(payload["stale_pending_auth_cleared"])


if __name__ == "__main__":
    unittest.main()
