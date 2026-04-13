"""Tests for binding_ui — CSRF protection, HTTP error handling, endpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from urllib import request as urlrequest

from labbook.binding_ui import start_binding_browser
from labbook.state import LabbookError


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
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://evil.example.com",
                    },
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
                    headers={
                        "Content-Type": "application/json",
                        "Origin": session.chooser_url.rstrip("/"),
                    },
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


if __name__ == "__main__":
    unittest.main()
