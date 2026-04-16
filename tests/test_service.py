from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import request as urlrequest


from labbook.binding_server import start_binding_server
from labbook.auth import (
    DEFAULT_NOTION_INTEGRATIONS_URL,
    NOTION_INTEGRATION_GUIDE_URL,
    clear_project_auth,
    configure_internal_integration,
    get_api_context,
    prepare_internal_integration,
    status,
)
from labbook.notion import (
    DEFAULT_SEARCH_PAGE_SIZE,
    NotionApiError,
    bind_resource_urls,
    bind_resources,
    discover_children,
    search_resources,
)
from labbook.state import (
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
]


class ServiceTests(unittest.TestCase):
    def test_status_recommends_prepare_when_no_token_exists(self) -> None:
        with mock.patch(
            "labbook.auth._available_storage_backends", return_value=BACKENDS_BOTH
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                payload = status(tmpdir)

        self.assertEqual(payload["integration"], "agent-labbook")
        self.assertFalse(payload["authenticated"])
        self.assertEqual(
            payload["recommended_action"], "notion_prepare_internal_integration"
        )
        self.assertEqual(payload["available_env_var"], TOKEN_ENV_VAR)
        self.assertFalse(payload["storage_choice_required"])
        self.assertEqual(
            payload["recommended_local_command"],
            "uvx agent-labbook configure-secret --storage keychain",
        )
        self.assertEqual(payload["secret_plan"]["mode"], "keychain")
        self.assertIn("default recommendation", payload["secret_plan"]["reason"])

    def test_prepare_internal_integration_returns_integrations_url(
        self,
    ) -> None:
        with mock.patch(
            "labbook.auth._available_storage_backends", return_value=BACKENDS_BOTH
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                payload = prepare_internal_integration(project_root=tmpdir)

        self.assertEqual(
            payload["notion_integrations_url"], DEFAULT_NOTION_INTEGRATIONS_URL
        )
        self.assertEqual(payload["notion_docs_url"], NOTION_INTEGRATION_GUIDE_URL)
        self.assertNotIn("browser_opened", payload)
        self.assertNotIn("open_browser_attempted", payload)
        self.assertEqual(payload["storage_default"], "keychain")
        self.assertFalse(payload["storage_choice_required"])
        self.assertEqual(
            payload["recommended_local_command"],
            "uvx agent-labbook configure-secret --storage keychain",
        )
        self.assertEqual(
            payload["recommended_next_action"],
            "notion_configure_internal_integration",
        )

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
            with mock.patch(
                "labbook.auth._available_storage_backends",
                return_value=BACKENDS_KEYCHAIN_ONLY,
            ):
                with mock.patch.dict(
                    os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
                ):
                    payload = status(tmpdir)

        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["token_source"], "env")
        self.assertTrue(payload["env_token_present"])
        self.assertEqual(payload["workspace_name"], "Workspace One")
        self.assertEqual(payload["recommended_action"], "notion_search_resources")
        self.assertEqual(payload["secret_plan"]["mode"], "keychain")

    def test_configure_internal_integration_auto_defaults_to_keychain_when_multiple_backends_exist(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            keyring_mock = mock.Mock()
            with mock.patch(
                "labbook.auth._available_storage_backends",
                return_value=BACKENDS_BOTH,
            ):
                with mock.patch("labbook.auth.NotionClient.get_me", return_value={}):
                    with mock.patch("labbook.storage.keyring", new=keyring_mock):
                        payload = configure_internal_integration(
                            secret="secret_test_token",
                            project_root=tmpdir,
                        )

        keyring_mock.set_password.assert_called_once()
        self.assertEqual(payload["storage"], "keychain")

    def test_configure_internal_integration_stores_secret_in_keyring_and_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            keyring_mock = mock.Mock()
            with mock.patch(
                "labbook.auth._available_storage_backends",
                return_value=BACKENDS_KEYCHAIN_ONLY,
            ):
                with mock.patch("labbook.auth.NotionClient.get_me") as get_me_mock:
                    with mock.patch("labbook.storage.keyring", new=keyring_mock):
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

    def test_search_resources_normalizes_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch("labbook.notion.NotionClient.search") as search_mock:
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

                    payload = search_resources(
                        project_root=tmpdir, query="spec", page_size=10
                    )

        self.assertEqual(payload["page_size"], 10)
        self.assertEqual(payload["result_count"], 2)
        resource_types = {item["resource_type"] for item in payload["results"]}
        self.assertEqual(resource_types, {"page", "data_source"})

    def test_bind_resources_preserves_subtree_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.retrieve_resource"
                ) as retrieve_mock:
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

    def test_bind_resource_urls_defaults_to_subtree_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.retrieve_resource"
                ) as retrieve_mock:
                    retrieve_mock.return_value = {
                        "object": "page",
                        "id": "01234567-89ab-cdef-0123-456789abcdef",
                        "url": "https://www.notion.so/example",
                        "title": [{"plain_text": "Project Home"}],
                    }

                    payload = bind_resource_urls(
                        project_root=tmpdir,
                        resource_urls=[
                            "https://www.notion.so/example-0123456789abcdef0123456789abcdef"
                        ],
                    )

        self.assertEqual(payload["resource_count"], 1)
        self.assertEqual(payload["resources"][0]["selection_scope"], "subtree")

    def test_bind_resources_resolves_database_container_to_single_data_source(
        self,
    ) -> None:
        database_id = "31d067f5-6067-8026-98d4-d1bc97f22287"
        data_source_id = "41d067f5-6067-8026-98d4-d1bc97f22287"
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.retrieve_data_source"
                ) as retrieve_data_source:
                    with mock.patch(
                        "labbook.notion.NotionClient.retrieve_database"
                    ) as retrieve_database:
                        retrieve_data_source.side_effect = [
                            NotionApiError(
                                f"Notion API 404: Could not find database with ID: {database_id}",
                                status_code=404,
                            ),
                            {
                                "object": "data_source",
                                "id": data_source_id,
                                "url": "https://www.notion.so/data-source",
                                "name": "Projects",
                            },
                        ]
                        retrieve_database.return_value = {
                            "object": "database",
                            "id": database_id,
                            "data_sources": [
                                {"id": data_source_id, "name": "Projects"}
                            ],
                        }

                        payload = bind_resources(
                            project_root=tmpdir,
                            resource_refs=[
                                {
                                    "resource_id": database_id,
                                    "resource_type": "data_source",
                                    "selection_scope": "subtree",
                                }
                            ],
                        )

        self.assertEqual(payload["resource_count"], 1)
        self.assertEqual(payload["resources"][0]["resource_id"], data_source_id)
        self.assertEqual(payload["resources"][0]["resource_type"], "data_source")

    def test_bind_resources_rejects_database_with_multiple_data_sources(self) -> None:
        database_id = "31d067f5-6067-8026-98d4-d1bc97f22287"
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.retrieve_data_source",
                    side_effect=NotionApiError(
                        f"Notion API 404: Could not find database with ID: {database_id}",
                        status_code=404,
                    ),
                ):
                    with mock.patch(
                        "labbook.notion.NotionClient.retrieve_database",
                        return_value={
                            "object": "database",
                            "id": database_id,
                            "data_sources": [
                                {
                                    "id": "41d067f5-6067-8026-98d4-d1bc97f22287",
                                    "name": "Projects",
                                },
                                {
                                    "id": "51d067f5-6067-8026-98d4-d1bc97f22287",
                                    "name": "Archive",
                                },
                            ],
                        },
                    ):
                        with self.assertRaises(LabbookError) as ctx:
                            bind_resources(
                                project_root=tmpdir,
                                resource_refs=[
                                    {
                                        "resource_id": database_id,
                                        "resource_type": "data_source",
                                        "selection_scope": "subtree",
                                    }
                                ],
                            )

        self.assertIn("multiple data sources", str(ctx.exception))

    def test_discover_children_finds_child_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.retrieve_resource"
                ) as retrieve_root:
                    with mock.patch(
                        "labbook.notion.NotionClient.list_block_children"
                    ) as list_children:
                        with mock.patch(
                            "labbook.notion.NotionClient.retrieve_page"
                        ) as retrieve_page:
                            retrieve_root.return_value = {
                                "object": "page",
                                "id": "01234567-89ab-cdef-0123-456789abcdef",
                                "url": "https://www.notion.so/root",
                                "title": [{"plain_text": "Root Page"}],
                            }
                            list_children.return_value = {
                                "results": [
                                    {
                                        "id": "11111111-2222-3333-4444-555555555555",
                                        "type": "child_page",
                                        "child_page": {"title": "Child Page"},
                                    }
                                ],
                                "has_more": False,
                                "next_cursor": None,
                            }
                            retrieve_page.return_value = {
                                "object": "page",
                                "id": "11111111-2222-3333-4444-555555555555",
                                "url": "https://www.notion.so/child",
                                "title": [{"plain_text": "Child Page"}],
                            }

                            payload = discover_children(
                                project_root=tmpdir,
                                resource_id_or_url="01234567-89ab-cdef-0123-456789abcdef",
                            )

        self.assertEqual(payload["page_size"], 50)
        self.assertEqual(payload["mode"], "shallow")
        self.assertFalse(payload["partial"])
        self.assertEqual(payload["root_resource"]["title"], "Root Page")
        self.assertEqual(payload["result_count"], 1)
        self.assertEqual(payload["results"][0]["title"], "Child Page")
        self.assertEqual(payload["results"][0]["discovered_depth"], 1)

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
            with mock.patch("labbook.storage.keyring", new=keyring_mock):
                payload = get_api_context(tmpdir)

        self.assertEqual(payload["token_source"], "keychain")
        self.assertEqual(payload["storage"], "keychain")
        self.assertEqual(payload["access_token"], "secret_keyring_token")
        self.assertEqual(payload["workspace_name"], "Workspace One")
        self.assertEqual(len(payload["bound_resources"]), 1)

    def test_status_recommends_current_configured_backend_when_it_is_working(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(
                tmpdir,
                {
                    "storage": "keychain",
                    "keyring_service": KEYRING_SERVICE_NAME,
                    "keyring_account": "project-root:/tmp/example",
                },
            )
            with mock.patch(
                "labbook.auth._available_storage_backends",
                return_value=BACKENDS_BOTH,
            ):
                with mock.patch(
                    "labbook.storage.keyring.get_password",
                    return_value="secret_keychain_token",
                ):
                    payload = status(tmpdir)

        self.assertEqual(payload["token_source"], "keychain")

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
            with mock.patch("labbook.storage.keyring", new=keyring_mock):
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

    def test_search_page_size_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ, {TOKEN_ENV_VAR: "secret_env_token"}, clear=False
            ):
                with mock.patch(
                    "labbook.notion.NotionClient.search",
                    return_value={"results": []},
                ) as search_mock:
                    payload = search_resources(project_root=tmpdir)

        self.assertEqual(payload["page_size"], DEFAULT_SEARCH_PAGE_SIZE)
        search_mock.assert_called_once()

    def test_binding_browser_serves_search_and_bind_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "labbook.auth.status",
                return_value={
                    "authenticated": True,
                    "workspace_name": "Workspace One",
                },
            ):
                with mock.patch(
                    "labbook.auth.notion_client_for_project",
                    return_value=(mock.Mock(), {"project_root": tmpdir}),
                ):
                    with mock.patch(
                        "labbook.notion.build_search_resources_payload",
                        return_value={
                            "project_root": tmpdir,
                            "query": None,
                            "page_size": 7,
                            "result_count": 1,
                            "results": [
                                {
                                    "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                                    "resource_type": "page",
                                    "resource_url": "https://www.notion.so/example",
                                    "title": "Project Home",
                                }
                            ],
                        },
                    ):
                        with mock.patch(
                            "labbook.notion.list_bindings",
                            return_value={
                                "project_root": tmpdir,
                                "resource_count": 0,
                                "resources": [],
                            },
                        ):
                            with mock.patch(
                                "labbook.notion.bind_resources",
                                return_value={
                                    "project_root": tmpdir,
                                    "resource_count": 1,
                                    "resources": [
                                        {
                                            "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                                            "resource_type": "page",
                                            "resource_url": "https://www.notion.so/example",
                                            "title": "Project Home",
                                            "alias": "project-home",
                                            "selection_scope": "subtree",
                                            "bound_at": "2026-04-10T00:00:00+00:00",
                                            "source": "manual_bind",
                                        }
                                    ],
                                },
                            ) as bind_mock:
                                session = start_binding_server(
                                    project_root=tmpdir,
                                    timeout_seconds=60,
                                    page_size=7,
                                )
                                try:
                                    root_html = (
                                        urlrequest.urlopen(
                                            session.chooser_url, timeout=5
                                        )
                                        .read()
                                        .decode("utf-8")
                                    )
                                    app_js = (
                                        urlrequest.urlopen(
                                            f"{session.chooser_url}assets/binding_chooser_app.js",
                                            timeout=5,
                                        )
                                        .read()
                                        .decode("utf-8")
                                    )
                                    search_payload = json.loads(
                                        urlrequest.urlopen(
                                            f"{session.chooser_url}api/search?page_size=7",
                                            timeout=5,
                                        )
                                        .read()
                                        .decode("utf-8")
                                    )
                                    request_payload = json.dumps(
                                        {
                                            "resource_refs": [
                                                {
                                                    "resource_id_or_url": "https://www.notion.so/example",
                                                    "selection_scope": "subtree",
                                                }
                                            ]
                                        }
                                    ).encode("utf-8")
                                    bind_response = json.loads(
                                        urlrequest.urlopen(
                                            urlrequest.Request(
                                                f"{session.chooser_url}api/bind",
                                                data=request_payload,
                                                headers={
                                                    "Content-Type": "application/json",
                                                    "X-Labbook-CSRF-Token": session.csrf_token,
                                                },
                                                method="POST",
                                            ),
                                            timeout=5,
                                        )
                                        .read()
                                        .decode("utf-8")
                                    )
                                finally:
                                    session.stop()

        self.assertIn('id="labbook-config"', root_html)
        self.assertIn('src="./assets/binding_chooser_app.js"', root_html)
        self.assertIn("projectTreeState", app_js)
        self.assertEqual(search_payload["result_count"], 1)
        self.assertEqual(bind_response["resource_count"], 1)
        bind_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
