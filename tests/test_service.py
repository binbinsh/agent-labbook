from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import json
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.service import (
    DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    MIN_BROWSER_AUTH_PAGE_LIMIT,
    _bindings_from_selected_resources,
    auth_browser,
    bind_resources,
    complete_headless_auth,
    get_api_context,
    normalize_browser_auth_page_limit,
    start_headless_auth,
    status,
)
from labbook.state import load_pending_auth, save_project_bindings, save_project_session


class ServiceTests(unittest.TestCase):
    def test_status_recommends_browser_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_auth_browser")
        self.assertEqual(
            payload["recommended_browser_auth_timeout_seconds"],
            DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
        )
        self.assertIn("same machine", payload["browser_auth_hint"])
        self.assertIn("notion_complete_headless_auth", payload["headless_auth_hint"])

    def test_page_limit_is_clamped_to_minimum(self) -> None:
        self.assertEqual(normalize_browser_auth_page_limit(None), DEFAULT_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(normalize_browser_auth_page_limit(5), MIN_BROWSER_AUTH_PAGE_LIMIT)

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

    def test_status_recommends_complete_headless_auth_for_any_pending_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".labbook"
            state_dir.mkdir(parents=True, exist_ok=True)
            pending_auth_path = state_dir / "pending-auth.json"
            pending_auth_path.write_text(
                json.dumps(
                    {
                        "mode": "local_browser",
                        "session_id": "pending-session",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_complete_headless_auth")

    def test_start_headless_auth_persists_clamped_page_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = start_headless_auth(project_root=tmpdir, page_limit=10)
            pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)

    def test_oauth_selection_bindings_preserve_subtree_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = _bindings_from_selected_resources(
                selected_resources=[
                    {
                        "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                        "resource_type": "page",
                        "title": "Project Home",
                        "selection_scope": "subtree",
                    }
                ],
                project_root=Path(tmpdir),
            )

        self.assertEqual(payload["resources"][0]["selection_scope"], "subtree")

    def test_auth_browser_auto_switches_to_headless_when_open_browser_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = auth_browser(project_root=tmpdir, page_limit=10, open_browser=False)
            pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["auth_mode"], "headless")
        self.assertTrue(payload["auto_switched_to_headless"])
        self.assertIn("open_browser=false", payload["reason"])
        self.assertEqual(payload["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_auth_browser_falls_back_to_headless_when_browser_cannot_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("labbook.service.webbrowser.open", return_value=False):
                payload = auth_browser(project_root=tmpdir, page_limit=10, open_browser=True)
                pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["auth_mode"], "headless")
        self.assertTrue(payload["auto_switched_to_headless"])
        self.assertIn("could not be opened", payload["reason"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_complete_headless_auth_accepts_pending_local_browser_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pending_auth_path = root / ".labbook" / "pending-auth.json"
            pending_auth_path.parent.mkdir(parents=True, exist_ok=True)
            pending_auth_path.write_text(
                json.dumps(
                    {
                        "mode": "local_browser",
                        "session_id": "local-browser-session",
                        "backend_url": "https://labbook.superplanner.net",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("labbook.service._complete_auth_handoff", return_value={"ok": True}) as complete_mock:
                payload = complete_headless_auth(project_root=root, handoff_bundle="bundle")

        self.assertEqual(payload, {"ok": True})
        complete_mock.assert_called_once()

    def test_api_context_exposes_selection_scope_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "workspace_name": "Workspace",
                    "workspace_id": "workspace-id",
                    "bot_id": "bot-id",
                },
            )
            save_project_bindings(
                root,
                {
                    "version": 1,
                    "project_root": str(root),
                    "default_resource_alias": "project-home",
                    "resources": [
                        {
                            "alias": "project-home",
                            "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                            "resource_type": "page",
                            "title": "Project Home",
                            "resource_url": "https://www.notion.so/example",
                            "source": "oauth_selection",
                            "bound_at": "2026-04-03T00:00:00+00:00",
                            "selection_scope": "subtree",
                        }
                    ],
                },
            )

            payload = get_api_context(root)

        self.assertEqual(payload["binding_model"], "explicit_roots_with_selection_scope")
        self.assertIn("selection_scope='subtree'", payload["selection_scope_note"])
        self.assertEqual(payload["resources"][0]["selection_scope"], "subtree")
        self.assertIn("${NOTION_TOKEN}", payload["curl_example"])
        self.assertNotIn("test-access-token", payload["curl_example"])

    def test_manual_bind_resources_can_request_subtree_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                },
            )

            mock_client = mock.Mock()
            mock_client.retrieve_resource.return_value = {
                "id": "01234567-89ab-cdef-0123-456789abcdef",
                "object": "page",
                "url": "https://www.notion.so/example",
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"plain_text": "Manual Root"}],
                    }
                },
            }

            with mock.patch("labbook.service._notion_client", return_value=mock_client):
                payload = bind_resources(
                    project_root=root,
                    resource_refs=[
                        {
                            "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                            "selection_scope": "subtree",
                        }
                    ],
                )

        self.assertEqual(payload["resources"][0]["selection_scope"], "subtree")


if __name__ == "__main__":
    unittest.main()
