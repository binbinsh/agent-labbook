from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labbook.service import (  # noqa: E402
    DEFAULT_NOTION_INTEGRATIONS_URL,
    DEFAULT_SEARCH_PAGE_SIZE,
    NOTION_INTEGRATION_GUIDE_URL,
    bind_resources,
    clear_project_auth,
    configure_internal_integration,
    get_api_context,
    prepare_internal_integration,
    search_resources,
    status,
)
from labbook.state import (  # noqa: E402
    KEYRING_SERVICE_NAME,
    TOKEN_ENV_VAR,
    LabbookError,
    load_project_bindings,
    load_project_session,
    save_project_bindings,
    save_project_session,
)


BACKENDS_BOTH = [
    {
        "backend": "keychain",
        "display_name": "System Keychain",
        "available": True,
        "selected_by_default": True,
        "reason": None,
        "details": {"keyring_backend": "Keyring"},
    },
    {
        "backend": "1password",
        "display_name": "1Password",
        "available": True,
        "selected_by_default": False,
        "reason": None,
        "details": {"cli_path": "/opt/homebrew/bin/op", "signed_in": True, "vaults": []},
    },
]

BACKENDS_KEYCHAIN_ONLY = [
    {
        "backend": "keychain",
        "display_name": "System Keychain",
        "available": True,
        "selected_by_default": True,
        "reason": None,
        "details": {"keyring_backend": "Keyring"},
    },
    {
        "backend": "1password",
        "display_name": "1Password",
        "available": False,
        "selected_by_default": False,
        "reason": "The 1Password CLI (`op`) is not installed.",
        "details": {"cli_path": None, "signed_in": False, "vaults": []},
    },
]


class ServiceTests(unittest.TestCase):
    def test_status_recommends_prepare_when_no_token_exists(self) -> None:
        with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_BOTH):
            with tempfile.TemporaryDirectory() as tmpdir:
                payload = status(tmpdir)

        self.assertEqual(payload["integration"], "notion-agent-labbook")
        self.assertFalse(payload["authenticated"])
        self.assertEqual(payload["recommended_action"], "notion_prepare_internal_integration")
        self.assertEqual(payload["secret_plan"]["mode"], "keychain")
        self.assertIn("default recommendation", payload["secret_plan"]["reason"])
        self.assertEqual(payload["available_env_var"], TOKEN_ENV_VAR)
        self.assertTrue(payload["storage_choice_required"])

    def test_prepare_internal_integration_opens_browser_and_reports_backends(self) -> None:
        with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_BOTH):
            with mock.patch("labbook.service._open_browser_url", return_value=True) as open_mock:
                with tempfile.TemporaryDirectory() as tmpdir:
                    payload = prepare_internal_integration(project_root=tmpdir, open_browser=True)

        open_mock.assert_called_once_with(DEFAULT_NOTION_INTEGRATIONS_URL)
        self.assertEqual(payload["notion_integrations_url"], DEFAULT_NOTION_INTEGRATIONS_URL)
        self.assertEqual(payload["notion_docs_url"], NOTION_INTEGRATION_GUIDE_URL)
        self.assertTrue(payload["browser_opened"])
        self.assertEqual(payload["storage_default"], "keychain")
        self.assertTrue(payload["storage_choice_required"])

    def test_status_prefers_environment_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "keychain",
                    "workspace_name": "Workspace One",
                    "keyring_service": KEYRING_SERVICE_NAME,
                    "keyring_account": "project-root:/tmp/example",
                },
            )
            with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_KEYCHAIN_ONLY):
                with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False):
                    payload = status(tmpdir)

        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["token_source"], "env")
        self.assertTrue(payload["env_token_present"])
        self.assertEqual(payload["secret_plan"]["mode"], "keychain")
        self.assertEqual(payload["workspace_name"], "Workspace One")
        self.assertEqual(payload["recommended_action"], "notion_search_resources")

    def test_configure_internal_integration_requires_explicit_choice_when_multiple_backends_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_BOTH):
                with mock.patch("labbook.service.NotionClient.get_me", return_value={}):
                    with self.assertRaises(LabbookError):
                        configure_internal_integration(
                            secret="secret_test_token",
                            project_root=tmpdir,
                        )

    def test_configure_internal_integration_stores_secret_in_keyring_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            keyring_mock = mock.Mock()
            with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_KEYCHAIN_ONLY):
                with mock.patch("labbook.service.NotionClient.get_me") as get_me_mock:
                    with mock.patch("labbook.service.keyring", new=keyring_mock):
                        get_me_mock.return_value = {
                            "id": "bot-user-id",
                            "bot": {
                                "workspace_name": "Workspace One",
                                "workspace_id": "workspace-id",
                                "owner": {"type": "workspace"},
                            },
                        }

                        payload = configure_internal_integration(
                            secret="secret_test_token",
                            project_root=tmpdir,
                            storage="keychain",
                        )
                        session_payload = load_project_session(tmpdir)

        keyring_mock.set_password.assert_called_once()
        self.assertEqual(payload["token_source"], "keychain")
        self.assertEqual(payload["storage"], "keychain")
        self.assertEqual(payload["workspace_name"], "Workspace One")
        self.assertEqual(payload["recommended_next_action"], "notion_search_resources")
        self.assertIsNotNone(session_payload)
        self.assertEqual(session_payload["storage"], "keychain")
        self.assertEqual(session_payload["workspace_name"], "Workspace One")
        self.assertEqual(session_payload["bot_id"], "bot-user-id")

    def test_configure_internal_integration_stores_secret_in_onepassword(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            onepassword_metadata = {
                "op_item_id": "item-123",
                "op_item_title": "Notion Agent Labbook",
                "op_vault": "Private",
                "op_vault_id": "vault-123",
                "op_ref": "op://vault-123/item-123/password",
            }
            with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_BOTH):
                with mock.patch("labbook.service._store_token_in_onepassword", return_value=onepassword_metadata) as store_mock:
                    with mock.patch("labbook.service.NotionClient.get_me", return_value={"id": "bot-user-id", "bot": {}}):
                        payload = configure_internal_integration(
                            secret="secret_test_token",
                            project_root=tmpdir,
                            storage="1password",
                            op_vault="Private",
                        )
                        session_payload = load_project_session(tmpdir)

        store_mock.assert_called_once()
        self.assertEqual(payload["token_source"], "1password")
        self.assertEqual(payload["storage"], "1password")
        self.assertIsNotNone(session_payload)
        self.assertEqual(session_payload["storage"], "1password")
        self.assertEqual(session_payload["op_item_id"], "item-123")

    def test_search_resources_normalizes_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False):
                with mock.patch("labbook.service.NotionClient.search") as search_mock:
                    search_mock.return_value = {
                        "results": [
                            {
                                "object": "page",
                                "id": "01234567-89ab-cdef-0123-456789abcdef",
                                "url": "https://www.notion.so/example",
                                "title": [{"plain_text": "Project Home"}],
                                "last_edited_time": "2026-04-10T00:00:00.000Z",
                            },
                            {
                                "object": "database",
                                "id": "fedcba98-7654-3210-fedc-ba9876543210",
                                "url": "https://www.notion.so/db",
                                "title": [{"plain_text": "Specs"}],
                            },
                        ]
                    }

                    payload = search_resources(project_root=tmpdir, query="spec", page_size=10)

        self.assertEqual(payload["page_size"], 10)
        self.assertEqual(payload["result_count"], 2)
        self.assertEqual(payload["results"][0]["resource_type"], "page")
        self.assertEqual(payload["results"][1]["resource_type"], "data_source")

    def test_bind_resources_preserves_subtree_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False):
                with mock.patch("labbook.service.NotionClient.retrieve_resource") as retrieve_mock:
                    retrieve_mock.return_value = {
                        "object": "page",
                        "id": "01234567-89ab-cdef-0123-456789abcdef",
                        "url": "https://www.notion.so/example",
                        "title": [{"plain_text": "Project Home"}],
                    }

                    payload = bind_resources(
                        project_root=tmpdir,
                        resource_refs=[
                            {
                                "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                                "selection_scope": "subtree",
                            }
                        ],
                        default_alias="project-home",
                    )
                    saved_payload = load_project_bindings(tmpdir)

        self.assertEqual(payload["default_resource_alias"], "project-home")
        self.assertEqual(payload["resources"][0]["selection_scope"], "subtree")
        self.assertIsNotNone(saved_payload)
        self.assertEqual(saved_payload["resources"][0]["selection_scope"], "subtree")

    def test_get_api_context_returns_bound_resources_for_keychain_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "keychain",
                    "workspace_name": "Workspace One",
                    "workspace_id": "workspace-id",
                    "bot_id": "bot-id",
                    "keyring_service": KEYRING_SERVICE_NAME,
                    "keyring_account": "project-root:/tmp/example",
                },
            )
            save_project_bindings(
                tmpdir,
                {
                    "project_root": tmpdir,
                    "default_resource_alias": "project-home",
                    "resources": [
                        {
                            "alias": "project-home",
                            "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                            "resource_type": "page",
                            "resource_url": "https://www.notion.so/example",
                            "title": "Project Home",
                            "source": "manual_bind",
                            "bound_at": "2026-04-10T00:00:00+00:00",
                            "selection_scope": "subtree",
                        }
                    ],
                },
            )
            keyring_mock = mock.Mock()
            keyring_mock.get_password.return_value = "secret_keyring_token"
            with mock.patch("labbook.service.keyring", new=keyring_mock):
                payload = get_api_context(tmpdir)

        self.assertEqual(payload["token_source"], "keychain")
        self.assertEqual(payload["storage"], "keychain")
        self.assertEqual(payload["access_token"], "secret_keyring_token")
        self.assertEqual(payload["workspace_name"], "Workspace One")
        self.assertEqual(len(payload["bound_resources"]), 1)

    def test_get_api_context_reads_secret_from_onepassword(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "1password",
                    "workspace_name": "Workspace One",
                    "workspace_id": "workspace-id",
                    "bot_id": "bot-id",
                    "op_item_id": "item-123",
                    "op_vault_id": "vault-123",
                    "op_ref": "op://vault-123/item-123/password",
                },
            )
            with mock.patch("labbook.service._op_command", return_value=("secret_op_token", None)):
                payload = get_api_context(tmpdir)

        self.assertEqual(payload["token_source"], "1password")
        self.assertEqual(payload["storage"], "1password")
        self.assertEqual(payload["access_token"], "secret_op_token")

    def test_status_recommends_current_configured_backend_when_it_is_working(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "1password",
                    "op_item_id": "item-123",
                    "op_vault_id": "vault-123",
                    "op_ref": "op://vault-123/item-123/password",
                },
            )
            with mock.patch("labbook.service._available_storage_backends", return_value=BACKENDS_BOTH):
                with mock.patch("labbook.service._op_command", return_value=("secret_op_token", None)):
                    payload = status(tmpdir)

        self.assertEqual(payload["token_source"], "1password")
        self.assertEqual(payload["secret_plan"]["mode"], "1password")
        self.assertIn("already using 1Password successfully", payload["secret_plan"]["reason"])

    def test_clear_project_auth_deletes_keyring_secret_and_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "keychain",
                    "keyring_service": KEYRING_SERVICE_NAME,
                    "keyring_account": "project-root:/tmp/example",
                },
            )
            save_project_bindings(
                tmpdir,
                {
                    "project_root": tmpdir,
                    "default_resource_alias": None,
                    "resources": [],
                },
            )
            keyring_mock = mock.Mock()
            with mock.patch("labbook.service.keyring", new=keyring_mock):
                payload = clear_project_auth(project_root=tmpdir, clear_bindings=True)

            remaining_session = load_project_session(tmpdir)
            remaining_bindings = load_project_bindings(tmpdir)

        keyring_mock.delete_password.assert_called_once()
        self.assertEqual(payload["storage"], "keychain")
        self.assertTrue(payload["session_cleared"])
        self.assertTrue(payload["stored_secret_deleted"])
        self.assertTrue(payload["bindings_cleared"])
        self.assertIsNone(remaining_session)
        self.assertIsNone(remaining_bindings)

    def test_clear_project_auth_deletes_onepassword_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "1password",
                    "op_item_id": "item-123",
                    "op_vault_id": "vault-123",
                },
            )
            with mock.patch("labbook.service._op_command", return_value=("", None)) as op_mock:
                payload = clear_project_auth(project_root=tmpdir)

        op_mock.assert_called_once()
        self.assertEqual(payload["storage"], "1password")
        self.assertTrue(payload["stored_secret_deleted"])

    def test_search_page_size_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False):
                with mock.patch("labbook.service.NotionClient.search", return_value={"results": []}) as search_mock:
                    payload = search_resources(project_root=tmpdir)

        self.assertEqual(payload["page_size"], DEFAULT_SEARCH_PAGE_SIZE)
        search_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
