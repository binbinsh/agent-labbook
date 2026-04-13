from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from labbook.state import (
    INTEGRATION_ID,
    LabbookError,
    load_project_bindings,
    load_project_session,
    save_project_bindings,
    save_project_session,
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
                        "storage": "keychain",
                        "workspace_name": "Legacy Workspace",
                    }
                ),
                encoding="utf-8",
            )

            payload = load_project_session(root)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["integration"], INTEGRATION_ID)
        self.assertEqual(payload["workspace_name"], "Legacy Workspace")

    def test_load_project_session_rejects_future_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = session_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": 999, "integration": INTEGRATION_ID}),
                encoding="utf-8",
            )

            with self.assertRaises(LabbookError) as exc_info:
                load_project_session(root)

        self.assertIn("future version", str(exc_info.exception))

    def test_load_project_session_rejects_unknown_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = session_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": 3, "integration": "different"}), encoding="utf-8"
            )

            with self.assertRaises(LabbookError) as exc_info:
                load_project_session(root)

        self.assertIn("expects", str(exc_info.exception))

    def test_save_project_session_injects_current_version_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "storage": "keychain",
                    "workspace_name": "Workspace",
                },
            )

            payload = load_project_session(root)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["integration"], INTEGRATION_ID)

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
