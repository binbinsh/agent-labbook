from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.state import (  # noqa: E402
    INTEGRATION_ID,
    LabbookError,
    load_project_bindings,
    load_project_session,
    load_pending_auth,
    pending_auth_path,
    save_pending_auth,
    save_project_bindings,
    session_path,
)


class StateVersioningTests(unittest.TestCase):
    def test_load_project_session_migrates_versionless_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = session_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "access_token": "legacy-access-token",
                        "refresh_token": "legacy-refresh-token",
                        "workspace_name": "Legacy Workspace",
                    }
                ),
                encoding="utf-8",
            )

            payload = load_project_session(root)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["integration"], INTEGRATION_ID)
        self.assertEqual(payload["workspace_name"], "Legacy Workspace")

    def test_load_project_session_rejects_future_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = session_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 999, "integration": INTEGRATION_ID}), encoding="utf-8")

            with self.assertRaises(LabbookError) as exc_info:
                load_project_session(root)

        self.assertIn("future version", str(exc_info.exception))

    def test_load_project_session_rejects_different_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = session_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 1, "integration": "different-integration"}), encoding="utf-8")

            with self.assertRaises(LabbookError) as exc_info:
                load_project_session(root)

        self.assertIn("expects", str(exc_info.exception))

    def test_save_pending_auth_injects_current_version_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_pending_auth(
                root,
                {
                    "mode": "headless",
                    "session_id": "pending-auth-session",
                    "auth_url": "https://superplanner.ai/notion/oauth/start",
                },
            )

            payload = load_pending_auth(root)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["integration"], INTEGRATION_ID)
        self.assertEqual(payload["session_id"], "pending-auth-session")

    def test_load_pending_auth_rejects_different_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = pending_auth_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 1, "integration": "different-integration"}), encoding="utf-8")

            with self.assertRaises(LabbookError) as exc_info:
                load_pending_auth(root)

        self.assertIn("expects", str(exc_info.exception))

    def test_save_project_bindings_injects_current_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_bindings(
                root,
                {
                    "project_root": str(root),
                    "default_resource_alias": None,
                    "resources": [],
                },
            )

            payload = load_project_bindings(root)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["resources"], [])


if __name__ == "__main__":
    unittest.main()
