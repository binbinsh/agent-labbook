from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from unittest import mock
import webbrowser


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.service import (
    BROKER_API_VERSION,
    BROKER_API_VERSIONS_HEADER,
    DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    MIN_BROWSER_AUTH_PAGE_LIMIT,
    NOTION_ACCESS_BROKER_SRC_ENV_VAR,
    _post_backend_json,
    _bindings_from_selected_resources,
    _candidate_notion_access_broker_src_paths,
    _open_browser_url,
    attach_saved_credential,
    auth_browser,
    bind_resources,
    complete_headless_auth,
    finalize_pending_auth,
    get_api_context,
    list_saved_credentials,
    normalize_browser_auth_page_limit,
    refresh_session,
    selection_browser,
    start_headless_auth,
    status,
)
from labbook.state import (
    DEFAULT_BACKEND_URL,
    DEFAULT_OAUTH_BASE_URL,
    LabbookError,
    clear_pending_auth,
    effective_backend_url,
    effective_oauth_base_url,
    load_pending_auth,
    load_pending_handoff,
    load_project_session,
    save_pending_handoff,
    save_project_bindings,
    save_project_session,
)


LOCAL_BROWSER_ENVIRONMENT = {
    "preferred_browser_flow": "local_browser",
    "recommended_open_browser": True,
    "ssh_session_detected": False,
    "display_detected": True,
    "graphical_launcher_available": True,
    "override_source": None,
    "reason": "No remote-session warning was detected, so a same-machine browser flow is acceptable.",
}


class ServiceTests(unittest.TestCase):
    def test_default_backend_uses_prefixed_superplanner_url(self) -> None:
        self.assertEqual(DEFAULT_BACKEND_URL, "https://superplanner.ai/notion/agent-labbook")

    def test_default_oauth_base_uses_shared_superplanner_url(self) -> None:
        self.assertEqual(DEFAULT_OAUTH_BASE_URL, "https://superplanner.ai/notion/oauth")

    def test_effective_backend_url_preserves_path_prefix(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_LABBOOK_BACKEND_URL": "https://example.com/notion/agent-labbook/"}):
            self.assertEqual(effective_backend_url(), "https://example.com/notion/agent-labbook")

    def test_effective_oauth_base_url_preserves_path_prefix(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_LABBOOK_OAUTH_BASE_URL": "https://example.com/notion/oauth/"}):
            self.assertEqual(effective_oauth_base_url(), "https://example.com/notion/oauth")

    def test_effective_backend_url_rejects_query_strings(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_LABBOOK_BACKEND_URL": "https://example.com/notion/agent-labbook?x=1"}):
            with self.assertRaises(LabbookError):
                effective_backend_url()

    def test_effective_oauth_base_url_rejects_query_strings(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_LABBOOK_OAUTH_BASE_URL": "https://example.com/notion/oauth?x=1"}):
            with self.assertRaises(LabbookError):
                effective_oauth_base_url()

    def test_status_recommends_browser_by_default(self) -> None:
        with mock.patch("labbook.service._list_saved_token_credentials", return_value=[]):
            with mock.patch("labbook.service._browser_environment", return_value=LOCAL_BROWSER_ENVIRONMENT.copy()):
                with tempfile.TemporaryDirectory() as tmpdir:
                    payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_auth_browser")
        self.assertEqual(payload["preferred_browser_flow"], "local_browser")
        self.assertTrue(payload["recommended_open_browser"])
        self.assertEqual(payload["backend_url"], DEFAULT_BACKEND_URL)
        self.assertEqual(payload["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)
        self.assertEqual(payload["redirect_uri"], f"{DEFAULT_OAUTH_BASE_URL}/callback")
        self.assertEqual(
            payload["recommended_browser_auth_timeout_seconds"],
            DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
        )
        self.assertIn("same machine", payload["browser_auth_hint"])
        self.assertIn("notion_complete_headless_auth", payload["headless_auth_hint"])

    def test_status_prefers_headless_when_ssh_detected(self) -> None:
        with mock.patch("labbook.service._list_saved_token_credentials", return_value=[]):
            with mock.patch.dict(os.environ, {"SSH_CONNECTION": "client 123 server 22"}, clear=False):
                with tempfile.TemporaryDirectory() as tmpdir:
                    payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_start_headless_auth")
        self.assertEqual(payload["preferred_browser_flow"], "headless")
        self.assertFalse(payload["recommended_open_browser"])
        self.assertTrue(payload["browser_environment"]["ssh_session_detected"])
        self.assertIn("SSH session variables were detected", payload["browser_environment_hint"])

    def test_page_limit_is_clamped_to_minimum(self) -> None:
        self.assertEqual(normalize_browser_auth_page_limit(None), DEFAULT_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(normalize_browser_auth_page_limit(5), MIN_BROWSER_AUTH_PAGE_LIMIT)

    def test_status_reports_stale_pending_auth_without_clearing(self) -> None:
        with mock.patch("labbook.service._list_saved_token_credentials", return_value=[]):
            with mock.patch("labbook.service._browser_environment", return_value=LOCAL_BROWSER_ENVIRONMENT.copy()):
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
                    pending_auth = load_pending_auth(tmpdir)

        self.assertIsNotNone(payload["pending_auth"])
        self.assertTrue(payload["pending_auth_stale"])
        self.assertEqual(payload["recommended_action"], "notion_auth_browser")
        self.assertIsNotNone(pending_auth)

    def test_status_recommends_status_for_pending_local_browser_auth(self) -> None:
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

        self.assertEqual(payload["recommended_action"], "notion_status")
        self.assertFalse(payload["pending_handoff_ready"])

    def test_status_recommends_complete_headless_auth_for_pending_headless_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".labbook"
            state_dir.mkdir(parents=True, exist_ok=True)
            pending_auth_path = state_dir / "pending-auth.json"
            pending_auth_path.write_text(
                json.dumps(
                    {
                        "mode": "headless",
                        "session_id": "pending-session",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )

            payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_complete_headless_auth")

    def test_status_recommends_finalize_for_ready_local_browser_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / ".labbook"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "pending-auth.json").write_text(
                json.dumps(
                    {
                        "mode": "local_browser",
                        "session_id": "pending-session",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            save_pending_handoff(
                root,
                {
                    "version": 1,
                    "project_root": str(root),
                    "session_id": "pending-session",
                    "handoff_bundle": "bundle",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "return_to": "http://127.0.0.1:8765/oauth/handoff",
                },
            )

            payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_finalize_pending_auth")
        self.assertTrue(payload["pending_handoff_ready"])

    def test_status_recommends_saved_credentials_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "labbook.service._credential_provider_diagnostics",
                return_value={
                    "requested_provider": "auto",
                    "resolved_provider": "1password",
                    "providers": [
                        {"provider": "1password", "available": True, "selected_by_default": True, "reason": None},
                        {"provider": "keyring", "available": True, "selected_by_default": False, "reason": None},
                    ],
                },
            ):
                with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[
                    {
                        "provider": "keyring",
                        "credential_ref": "cred-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                        "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                    }
                ],
                ):
                    payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_attach_saved_credential")
        self.assertEqual(payload["available_saved_credentials_count"], 1)
        self.assertEqual(payload["available_saved_credentials"][0]["display_name"], "Workspace One")
        self.assertEqual(payload["credential_provider_diagnostics"]["resolved_provider"], "1password")

    def test_status_recommends_setup_guide_when_saved_credential_lookup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("labbook.service._credential_provider_diagnostics", return_value=None):
                with mock.patch(
                    "labbook.service._annotated_saved_credentials",
                    side_effect=LabbookError("The shared notion-access-broker Python helpers are not installed."),
                ):
                    payload = status(tmpdir)

        self.assertEqual(payload["recommended_action"], "notion_setup_guide")
        self.assertIn("not installed", payload["saved_credentials_error"])

    def test_candidate_broker_src_paths_include_env_and_cwd_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_repo = root / "custom-broker"
            project_root = root / "projects" / "doc-verbalizer"
            project_root.mkdir(parents=True, exist_ok=True)
            expected_auto = root / "projects" / "notion-access-broker" / "src"

            with mock.patch.dict(os.environ, {NOTION_ACCESS_BROKER_SRC_ENV_VAR: str(env_repo)}):
                paths = _candidate_notion_access_broker_src_paths(cwd=project_root)

        self.assertGreaterEqual(len(paths), 2)
        self.assertEqual(paths[0], env_repo.resolve())
        self.assertIn(expected_auto.resolve(), paths)

    def test_start_headless_auth_persists_clamped_page_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = start_headless_auth(project_root=tmpdir, page_limit=10)
            pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(payload["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)
        self.assertIn(f"{DEFAULT_OAUTH_BASE_URL}/start?", payload["auth_url"])
        self.assertIn("integration=agent-labbook", payload["auth_url"])
        self.assertIn("continue_to=", payload["auth_url"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["integration"], "agent-labbook")
        self.assertEqual(pending_auth["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(pending_auth["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)

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
        self.assertEqual(payload["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_auth_browser_falls_back_to_headless_when_browser_cannot_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("labbook.service._browser_environment", return_value=LOCAL_BROWSER_ENVIRONMENT.copy()):
                with mock.patch(
                    "labbook.service._spawn_persistent_local_handoff_server",
                    return_value={"return_to": "http://127.0.0.1:8765/oauth/handoff", "session_id": "test-session"},
                ):
                    with mock.patch("labbook.service._open_browser_url", return_value=False):
                        payload = auth_browser(project_root=tmpdir, page_limit=10, open_browser=True)
                        pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["auth_mode"], "headless")
        self.assertTrue(payload["auto_switched_to_headless"])
        self.assertIn("could not be opened", payload["reason"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_auth_browser_auto_switches_to_headless_when_ssh_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"SSH_CONNECTION": "client 123 server 22"}, clear=False):
                with mock.patch("labbook.service._spawn_persistent_local_handoff_server") as spawn_mock:
                    payload = auth_browser(project_root=tmpdir, page_limit=10, open_browser=True)
                    pending_auth = load_pending_auth(tmpdir)

        spawn_mock.assert_not_called()
        self.assertEqual(payload["auth_mode"], "headless")
        self.assertTrue(payload["auto_switched_to_headless"])
        self.assertIn("SSH session variables were detected", payload["reason"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_auth_browser_starts_async_local_browser_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("labbook.service._browser_environment", return_value=LOCAL_BROWSER_ENVIRONMENT.copy()):
                with mock.patch(
                    "labbook.service._spawn_persistent_local_handoff_server",
                    return_value={
                        "return_to": "http://127.0.0.1:8765/oauth/handoff",
                        "session_id": "test-session",
                        "pid": 12345,
                    },
                ):
                    with mock.patch("labbook.service._open_browser_url", return_value=True):
                        payload = auth_browser(project_root=tmpdir, page_limit=10, open_browser=True)
                        pending_auth = load_pending_auth(tmpdir)

        self.assertEqual(payload["auth_mode"], "local_browser")
        self.assertEqual(payload["recommended_next_action"], "notion_status")
        self.assertEqual(payload["page_limit"], MIN_BROWSER_AUTH_PAGE_LIMIT)
        self.assertEqual(payload["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)
        self.assertIn(f"{DEFAULT_OAUTH_BASE_URL}/start?", payload["auth_url"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "local_browser")
        self.assertEqual(pending_auth["return_to"], "http://127.0.0.1:8765/oauth/handoff")
        self.assertEqual(pending_auth["oauth_base_url"], DEFAULT_OAUTH_BASE_URL)

    def test_finalize_pending_auth_completes_saved_local_browser_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            resolved_root = root.resolve()
            state_dir = root / ".labbook"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "pending-auth.json").write_text(
                json.dumps(
                    {
                        "mode": "local_browser",
                        "session_id": "local-browser-session",
                        "backend_url": "https://superplanner.ai/notion/agent-labbook",
                        "oauth_base_url": "https://superplanner.ai/notion/oauth",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            save_pending_handoff(
                root,
                {
                    "version": 1,
                    "project_root": str(root),
                    "session_id": "local-browser-session",
                    "handoff_bundle": "bundle",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "return_to": "http://127.0.0.1:8765/oauth/handoff",
                },
            )

            def complete_side_effect(*, project_root: Path, pending_auth: dict, handoff_bundle: str) -> dict:
                self.assertEqual(project_root, resolved_root)
                self.assertEqual(pending_auth["session_id"], "local-browser-session")
                self.assertEqual(handoff_bundle, "bundle")
                save_project_session(
                    root,
                    {
                        "access_token": "test-access-token",
                        "refresh_token": "test-refresh-token",
                        "workspace_name": "Workspace",
                        "workspace_id": "workspace-id",
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
                clear_pending_auth(root)
                return {"ok": True}

            with mock.patch("labbook.service._complete_auth_handoff", side_effect=complete_side_effect):
                payload = finalize_pending_auth(project_root=tmpdir)
                remaining_handoff = load_pending_handoff(root)

        self.assertEqual(payload, {"ok": True})
        self.assertIsNone(remaining_handoff)

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
                        "backend_url": "https://superplanner.ai/notion/agent-labbook",
                        "oauth_base_url": "https://superplanner.ai/notion/oauth",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("labbook.service._complete_auth_handoff", return_value={"ok": True}) as complete_mock:
                payload = complete_headless_auth(project_root=root, handoff_bundle="bundle")

        self.assertEqual(payload, {"ok": True})
        complete_mock.assert_called_once()

    def test_post_backend_json_requires_expected_api_version(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = json.dumps(
            {
                "ok": True,
                "api_version": 999,
                "supported_api_versions": [999],
            }
        ).encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with mock.patch("labbook.service.request.urlopen", return_value=response):
            with self.assertRaises(LabbookError) as exc_info:
                _post_backend_json("https://superplanner.ai/notion/oauth/api/refresh", {"integration": "agent-labbook"})

        self.assertIn("API version mismatch", str(exc_info.exception))

    def test_post_backend_json_sends_accepted_api_versions_header(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = json.dumps(
            {
                "ok": True,
                "api_version": BROKER_API_VERSION,
                "supported_api_versions": [BROKER_API_VERSION],
            }
        ).encode("utf-8")
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with mock.patch("labbook.service.request.urlopen", return_value=response) as urlopen_mock:
            _post_backend_json("https://superplanner.ai/notion/oauth/api/refresh", {"integration": "agent-labbook"})

        request_obj = urlopen_mock.call_args.args[0]
        self.assertEqual(
            request_obj.headers["X-notion-access-broker-accept-api-versions"],
            BROKER_API_VERSIONS_HEADER,
        )

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

    def test_refresh_session_uses_shared_oauth_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "backend_url": "https://superplanner.ai/notion/agent-labbook",
                    "oauth_base_url": "https://superplanner.ai/notion/oauth",
                },
            )

            with mock.patch(
                "labbook.service._post_backend_json",
                return_value={
                    "ok": True,
                    "api_version": BROKER_API_VERSION,
                    "supported_api_versions": [BROKER_API_VERSION],
                    "token": {
                        "access_token": "refreshed-access-token",
                        "refresh_token": "refreshed-refresh-token",
                        "workspace_name": "Workspace",
                        "workspace_id": "workspace-id",
                    },
                },
            ) as post_backend_mock:
                with mock.patch(
                    "labbook.service._store_token_credential",
                    return_value={
                        "provider": "keyring",
                        "credential_ref": "cred-123",
                        "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                    },
                ):
                    payload = refresh_session(root)
                    saved_session = load_project_session(root)

        self.assertEqual(payload["oauth_base_url"], "https://superplanner.ai/notion/oauth")
        self.assertEqual(payload["backend_url"], "https://superplanner.ai/notion/agent-labbook")
        post_backend_mock.assert_called_once_with(
            "https://superplanner.ai/notion/oauth/api/refresh",
            {
                "integration": "agent-labbook",
                "refresh_token": "test-refresh-token",
            },
        )
        self.assertIsNotNone(saved_session)
        self.assertEqual(saved_session["credential_provider"], "keyring")
        self.assertEqual(saved_session["credential_ref"], "cred-123")
        self.assertNotIn("access_token", saved_session)
        self.assertNotIn("refresh_token", saved_session)

    def test_list_saved_credentials_marks_project_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "credential_provider": "keyring",
                    "credential_ref": "cred-1",
                },
            )
            with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[
                    {
                        "provider": "keyring",
                        "credential_ref": "cred-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                        "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                    },
                    {
                        "provider": "keyring",
                        "credential_ref": "cred-2",
                        "workspace_name": "Workspace Two",
                        "workspace_id": "workspace-2",
                        "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                    },
                ],
            ):
                payload = list_saved_credentials(project_root=root)

        self.assertEqual(payload["saved_credential_count"], 2)
        self.assertTrue(payload["credentials"][0]["attached_to_project"])
        self.assertFalse(payload["credentials"][1]["attached_to_project"])

    def test_attach_saved_credential_persists_reference_without_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[
                    {
                        "provider": "keyring",
                        "credential_ref": "cred-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                        "bot_id": "bot-1",
                        "authorized_at": "2026-04-04T00:00:00+00:00",
                        "updated_at": "2026-04-04T01:00:00+00:00",
                        "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                    }
                ],
            ):
                with mock.patch(
                    "labbook.service._load_token_credential",
                    return_value={
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                        "bot_id": "bot-1",
                    },
                ):
                    payload = attach_saved_credential(project_root=root)
                    saved_session = load_project_session(root)

        self.assertTrue(payload["attached_existing_credential"])
        self.assertEqual(payload["credential_ref"], "cred-1")
        self.assertIsNotNone(saved_session)
        self.assertEqual(saved_session["credential_provider"], "keyring")
        self.assertEqual(saved_session["credential_ref"], "cred-1")
        self.assertEqual(saved_session["session_source"], "saved_credential")
        self.assertNotIn("access_token", saved_session)
        self.assertNotIn("refresh_token", saved_session)

    def test_attach_saved_credential_requires_explicit_ref_when_multiple_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[
                    {"provider": "keyring", "credential_ref": "cred-1"},
                    {"provider": "keyring", "credential_ref": "cred-2"},
                ],
            ):
                with self.assertRaises(LabbookError):
                    attach_saved_credential(project_root=tmpdir)

    def test_attach_saved_credential_refuses_to_overwrite_bindings_without_clear_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
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
                            "source": "manual_bind",
                            "bound_at": "2026-04-03T00:00:00+00:00",
                            "selection_scope": "resource",
                        }
                    ],
                },
            )
            with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[{"provider": "keyring", "credential_ref": "cred-1"}],
            ):
                with self.assertRaises(LabbookError):
                    attach_saved_credential(project_root=root)

    def test_selection_browser_reuses_saved_credential_without_oauth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch("labbook.service._browser_environment", return_value=LOCAL_BROWSER_ENVIRONMENT.copy()):
                with mock.patch(
                    "labbook.service._list_saved_token_credentials",
                    return_value=[
                        {
                            "provider": "keyring",
                            "credential_ref": "cred-1",
                            "workspace_name": "Workspace One",
                            "workspace_id": "workspace-1",
                            "metadata": {"service_name": "notion-access-broker/agent-labbook"},
                        }
                    ],
                ):
                    with mock.patch(
                        "labbook.service._load_token_credential",
                        return_value={
                            "access_token": "access-1",
                            "refresh_token": "refresh-1",
                            "workspace_name": "Workspace One",
                            "workspace_id": "workspace-1",
                        },
                    ):
                        with mock.patch(
                            "labbook.service._spawn_persistent_local_handoff_server",
                            return_value={"return_to": "http://127.0.0.1:8765/oauth/handoff", "session_id": "server-session"},
                        ):
                            with mock.patch(
                                "labbook.service._post_backend_json",
                                return_value={
                                    "ok": True,
                                    "api_version": BROKER_API_VERSION,
                                    "supported_api_versions": [BROKER_API_VERSION],
                                    "continue_url": "https://superplanner.ai/notion/agent-labbook/oauth/continue?oauth_session=reused-session",
                                },
                            ) as post_backend_mock:
                                with mock.patch("labbook.service._open_browser_url", return_value=True):
                                    payload = selection_browser(project_root=root, replace_existing_bindings=False)
                                    pending_auth = load_pending_auth(root)

        self.assertEqual(payload["selection_mode"], "local_browser")
        self.assertEqual(payload["credential_ref"], "cred-1")
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["auth_url"], "https://superplanner.ai/notion/agent-labbook/oauth/continue?oauth_session=reused-session")
        post_backend_mock.assert_called_once()
        self.assertEqual(
            post_backend_mock.call_args[0][0],
            "https://superplanner.ai/notion/oauth/api/create-session",
        )

    def test_selection_browser_auto_switches_to_headless_when_ssh_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch(
                "labbook.service._list_saved_token_credentials",
                return_value=[
                    {
                        "provider": "keyring",
                        "credential_ref": "cred-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                    }
                ],
            ):
                with mock.patch(
                    "labbook.service._load_token_credential",
                    return_value={
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "workspace_name": "Workspace One",
                        "workspace_id": "workspace-1",
                    },
                ):
                    with mock.patch(
                        "labbook.service._post_backend_json",
                        return_value={
                            "ok": True,
                            "api_version": BROKER_API_VERSION,
                            "supported_api_versions": [BROKER_API_VERSION],
                            "continue_url": "https://superplanner.ai/notion/agent-labbook/oauth/continue?oauth_session=reused-session",
                        },
                    ):
                        with mock.patch.dict(os.environ, {"SSH_CONNECTION": "client 123 server 22"}, clear=False):
                            with mock.patch("labbook.service._spawn_persistent_local_handoff_server") as spawn_mock:
                                payload = selection_browser(project_root=root, replace_existing_bindings=False)
                                pending_auth = load_pending_auth(root)

        spawn_mock.assert_not_called()
        self.assertEqual(payload["selection_mode"], "headless")
        self.assertTrue(payload["auto_switched_to_headless"])
        self.assertIn("SSH session variables were detected", payload["reason"])
        self.assertIsNotNone(pending_auth)
        self.assertEqual(pending_auth["mode"], "headless")

    def test_open_browser_url_prefers_graphical_launcher_over_text_browser_default(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None

        def which_side_effect(name: str) -> str | None:
            return "/usr/bin/xdg-open" if name == "xdg-open" else None

        with mock.patch("labbook.service.shutil.which", side_effect=which_side_effect):
            with mock.patch("labbook.service.subprocess.Popen", return_value=process) as popen_mock:
                with mock.patch("labbook.service.webbrowser.get") as get_mock:
                    opened = _open_browser_url("https://example.com")

        self.assertTrue(opened)
        get_mock.assert_not_called()
        popen_mock.assert_called_once()
        self.assertEqual(
            popen_mock.call_args[0][0],
            ["/usr/bin/xdg-open", "https://example.com"],
        )

    def test_open_browser_url_rejects_text_browser_controller_without_graphical_launcher(self) -> None:
        controller = mock.Mock(spec=webbrowser.GenericBrowser)
        controller.name = "www-browser"
        controller.args = ["%s"]

        with mock.patch("labbook.service.shutil.which", return_value=None):
            with mock.patch("labbook.service.webbrowser.get", return_value=controller):
                opened = _open_browser_url("https://example.com")

        self.assertFalse(opened)

    def test_selection_browser_requires_explicit_binding_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_project_session(
                root,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
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
                            "source": "manual_bind",
                            "bound_at": "2026-04-03T00:00:00+00:00",
                            "selection_scope": "resource",
                        }
                    ],
                },
            )

            with self.assertRaises(LabbookError):
                selection_browser(project_root=root)

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
