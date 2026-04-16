"""Tests for binding_ui — CSRF protection, HTTP error handling, endpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from urllib import request as urlrequest

from labbook.binding_ui import start_binding_browser
from labbook.state import LabbookError, bindings_path, load_project_bindings


class BindingBrowserCsrfTests(unittest.TestCase):
    """Test CSRF origin validation on POST endpoints."""

    def _start_session(self, tmpdir: str) -> mock.Mock:
        """Start a binding browser session with mocked dependencies."""
        return start_binding_browser(
            project_root=tmpdir,
            open_browser=False,
            timeout_seconds=60,
            page_size=7,
        )

    @staticmethod
    def _csrf_headers(session, **extra: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Labbook-CSRF-Token": session.csrf_token,
            **extra,
        }

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_cross_origin_post_rejected(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = self._start_session(tmpdir)
            try:
                # POST with a different Origin header should be rejected
                req = urlrequest.Request(
                    f"{session.chooser_url}api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(
                        session, Origin="https://evil.example.com"
                    ),
                    method="POST",
                )
                try:
                    urlrequest.urlopen(req, timeout=5)
                    self.fail("Expected HTTP 403")
                except Exception as exc:
                    self.assertIn("403", str(exc))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_same_origin_post_accepted(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = self._start_session(tmpdir)
            try:
                req = urlrequest.Request(
                    f"{session.chooser_url}api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(
                        session, Origin=session.chooser_url.rstrip("/")
                    ),
                    method="POST",
                )
                response = urlrequest.urlopen(req, timeout=5)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_loopback_alias_origin_post_accepted(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = self._start_session(tmpdir)
            local_origin = session.chooser_url.rstrip("/").replace("127.0.0.1", "localhost")
            try:
                req = urlrequest.Request(
                    f"{session.chooser_url}api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(session, Origin=local_origin),
                    method="POST",
                )
                response = urlrequest.urlopen(req, timeout=5)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_null_origin_with_same_referer_post_accepted(
        self, *_mocks: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = self._start_session(tmpdir)
            try:
                req = urlrequest.Request(
                    f"{session.chooser_url}api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(
                        session, Origin="null", Referer=session.chooser_url
                    ),
                    method="POST",
                )
                response = urlrequest.urlopen(req, timeout=5)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_missing_csrf_token_rejected(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = self._start_session(tmpdir)
            try:
                req = urlrequest.Request(
                    f"{session.chooser_url}api/shutdown",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlrequest.urlopen(req, timeout=5)
                    self.fail("Expected HTTP 403")
                except Exception as exc:
                    self.assertIn("403", str(exc))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_remote_public_base_url_origin_accepted(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_browser(
                project_root=tmpdir,
                open_browser=False,
                timeout_seconds=60,
                page_size=7,
                host="0.0.0.0",
                public_base_url="https://remote.example.com/notion-bindings/",
            )
            try:
                response = urlrequest.urlopen(
                    f"{session.local_url}notion-bindings/", timeout=5
                )
                html = response.read().decode("utf-8")
                self.assertIn("Choose Notion Content", html)

                req = urlrequest.Request(
                    f"{session.local_url}notion-bindings/api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(
                        session, Origin="https://remote.example.com"
                    ),
                    method="POST",
                )
                response = urlrequest.urlopen(req, timeout=5)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_direct_remote_host_origin_accepted_without_public_base_url(
        self, *_mocks: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_browser(
                project_root=tmpdir,
                open_browser=False,
                timeout_seconds=60,
                page_size=7,
                host="0.0.0.0",
            )
            remote_host = f"172.16.0.88:{session.bind_port}"
            remote_origin = f"http://{remote_host}"
            try:
                response = urlrequest.urlopen(
                    urlrequest.Request(
                        session.local_url,
                        headers={"Host": remote_host},
                    ),
                    timeout=5,
                )
                html = response.read().decode("utf-8")
                self.assertIn(f'"api_base_url": "{remote_origin}/"', html)

                req = urlrequest.Request(
                    f"{session.local_url}api/shutdown",
                    data=b"{}",
                    headers=self._csrf_headers(
                        session,
                        Origin=remote_origin,
                        Host=remote_host,
                    ),
                    method="POST",
                )
                response = urlrequest.urlopen(req, timeout=5)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
            finally:
                session.stop()


class BindingBrowserUnauthenticatedTests(unittest.TestCase):
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={"authenticated": False},
    )
    def test_unauthenticated_project_raises(self, _status_mock: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(LabbookError) as ctx:
                start_binding_browser(
                    project_root=tmpdir,
                    open_browser=False,
                )
            self.assertIn("not authenticated", str(ctx.exception))


class BindingBrowserGetEndpointTests(unittest.TestCase):
    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_get_404_for_unknown_path(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_browser(
                project_root=tmpdir,
                open_browser=False,
                timeout_seconds=60,
                page_size=7,
            )
            try:
                try:
                    urlrequest.urlopen(
                        f"{session.chooser_url}api/nonexistent", timeout=5
                    )
                    self.fail("Expected HTTP 404")
                except Exception as exc:
                    self.assertIn("404", str(exc))
            finally:
                session.stop()

    @mock.patch("labbook.binding_ui.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.binding_ui.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch(
        "labbook.binding_ui.build_search_resources_payload",
        return_value={
            "results": [],
            "result_count": 0,
            "page_size": 7,
            "project_root": "/tmp",
        },
    )
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_get_bindings_endpoint(self, *_mocks: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_browser(
                project_root=tmpdir,
                open_browser=False,
                timeout_seconds=60,
                page_size=7,
            )
            try:
                response = urlrequest.urlopen(
                    f"{session.chooser_url}api/bindings", timeout=5
                )
                payload = json.loads(response.read().decode("utf-8"))
                self.assertIn("resources", payload)
            finally:
                session.stop()


class BindingBrowserBindEndpointTests(unittest.TestCase):
    @mock.patch(
        "labbook.binding_ui.status",
        return_value={
            "authenticated": True,
            "workspace_name": "Test",
            "binding_recommendation": None,
            "binding_options": [],
            "binding_question": None,
        },
    )
    def test_bind_persists_bindings_file(
        self, _status_mock: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            expected_bindings_path = str(bindings_path(tmpdir))
            fake_client = mock.Mock()
            fake_client.resolve_bindable_resource.return_value = (
                {
                    "object": "page",
                    "id": "01234567-89ab-cdef-0123-456789abcdef",
                    "url": "https://www.notion.so/project-home-0123456789abcdef0123456789abcdef",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Project Home"}],
                        }
                    }
                },
                "page",
            )
            session = start_binding_browser(
                project_root=tmpdir,
                open_browser=False,
                timeout_seconds=60,
                page_size=7,
            )
            try:
                with mock.patch(
                    "labbook.auth_flow.notion_client_for_project",
                    side_effect=lambda project_root=None: (
                        fake_client,
                        {"project_root": str(project_root)},
                    ),
                ):
                    request_payload = json.dumps(
                        {
                            "resource_refs": [
                                {
                                    "resource_id_or_url": "https://www.notion.so/project-home-0123456789abcdef0123456789abcdef",
                                    "selection_scope": "subtree",
                                }
                            ]
                        }
                    ).encode("utf-8")
                    response = urlrequest.urlopen(
                        urlrequest.Request(
                            f"{session.chooser_url}api/bind",
                            data=request_payload,
                            headers={
                                "Content-Type": "application/json",
                                "Origin": session.chooser_url.rstrip("/"),
                                "X-Labbook-CSRF-Token": session.csrf_token,
                            },
                            method="POST",
                        ),
                        timeout=5,
                    )
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                session.stop()

            saved_payload = load_project_bindings(tmpdir)
            bindings_file_exists = bindings_path(tmpdir).exists()

        self.assertEqual(payload["resource_count"], 1)
        self.assertEqual(payload["bindings_path"], expected_bindings_path)
        self.assertTrue(bindings_file_exists)
        self.assertIsNotNone(saved_payload)
        self.assertEqual(saved_payload["bindings_path"], expected_bindings_path)
        self.assertEqual(len(saved_payload["resources"]), 1)
        self.assertEqual(saved_payload["resources"][0]["title"], "Project Home")
        self.assertEqual(saved_payload["resources"][0]["selection_scope"], "subtree")


if __name__ == "__main__":
    unittest.main()
