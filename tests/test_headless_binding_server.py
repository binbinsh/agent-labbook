"""Regression tests for headless-friendly behaviour of start_binding_server.

These pin the guarantees introduced to prevent Codex MCP "Transport closed"
failures on SSH hosts:

1. Headless hosts bind 0.0.0.0 by default so SSH clients can reach the chooser
   directly (no port-forwarding required).
2. The MCP server never spawns a system browser; the payload exposes lan_urls
   for the agent to share with the user instead.
3. The wildcard-bind payload includes a security note explaining that no
   cookies are set and that the CSRF token is request-header only, so binding
   to 0.0.0.0 cannot leak session state across ports on the same host.
4. When bound to 0.0.0.0 without a public_base_url, requests from the
   advertised LAN URLs are accepted by the same-origin CSRF check.
5. notion_start_binding_server handler returns a structured error instead of
   hanging the MCP stdio transport if the underlying call exceeds its hard
   timeout.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from unittest import mock
from urllib import request as urlrequest

from labbook.binding_server import start_binding_server
from labbook.server import handle_call_tool


_AUTH_STATUS = {
    "authenticated": True,
    "likely_headless": True,
    "workspace_name": "Test",
    "binding_recommendation": None,
    "binding_options": [],
    "binding_question": None,
}


class HeadlessBindHostTests(unittest.TestCase):
    @mock.patch("labbook.auth.status", return_value=_AUTH_STATUS)
    def test_headless_defaults_to_wildcard_bind(self, _status_mock: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_server(
                project_root=tmpdir,
                timeout_seconds=60,
                page_size=7,
            )
            try:
                self.assertEqual(session.bind_host, "0.0.0.0")
                self.assertTrue(session.wildcard_bind)
                # Security notes advertise the no-cookie guarantee.
                payload = session.payload()
                self.assertTrue(payload["wildcard_bind"])
                notes = " ".join(payload["security_notes"]).lower()
                self.assertIn("cookie", notes)
                self.assertIn("csrf", notes)
                # Payload exposes a list (possibly empty in CI) of LAN URLs.
                self.assertIsInstance(payload["lan_urls"], list)
                # Payload MUST NOT reference any browser-launch outcome.
                self.assertNotIn("browser_opened", payload)
                self.assertNotIn("open_browser_attempted", payload)
            finally:
                session.stop()

    @mock.patch("labbook.auth.status", return_value=_AUTH_STATUS)
    def test_explicit_host_127_wins_even_on_headless(
        self, _status_mock: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_server(
                project_root=tmpdir,
                timeout_seconds=60,
                page_size=7,
                host="127.0.0.1",
            )
            try:
                self.assertEqual(session.bind_host, "127.0.0.1")
                self.assertFalse(session.wildcard_bind)
                payload = session.payload()
                self.assertEqual(payload["lan_urls"], [])
                self.assertEqual(payload["security_notes"], [])
            finally:
                session.stop()

    @mock.patch(
        "labbook.auth.status",
        return_value={**_AUTH_STATUS, "likely_headless": False},
    )
    def test_desktop_defaults_to_loopback_bind(self, _status_mock: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = start_binding_server(
                project_root=tmpdir,
                timeout_seconds=60,
                page_size=7,
            )
            try:
                self.assertEqual(session.bind_host, "127.0.0.1")
                self.assertFalse(session.wildcard_bind)
            finally:
                session.stop()


class WildcardOriginTrustTests(unittest.TestCase):
    """With 0.0.0.0 + no public_base_url, LAN-origin requests must be accepted."""

    @mock.patch("labbook.notion.list_bindings", return_value={"resources": []})
    @mock.patch(
        "labbook.auth.notion_client_for_project",
        return_value=(mock.Mock(), {"project_root": "/tmp/test"}),
    )
    @mock.patch("labbook.auth.status", return_value=_AUTH_STATUS)
    def test_synthetic_lan_url_origin_accepted(self, *_mocks: mock.Mock) -> None:
        with mock.patch(
            "labbook.binding_server._enumerate_lan_hosts",
            return_value=["192.0.2.17"],
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                session = start_binding_server(
                    project_root=tmpdir,
                    timeout_seconds=60,
                    page_size=7,
                )
                try:
                    self.assertEqual(session.bind_host, "0.0.0.0")
                    self.assertIn(
                        f"http://192.0.2.17:{session.bind_port}/",
                        session.lan_urls,
                    )
                    remote_host = f"192.0.2.17:{session.bind_port}"
                    remote_origin = f"http://{remote_host}"
                    req = urlrequest.Request(
                        f"{session.local_url}api/shutdown",
                        data=b"{}",
                        headers={
                            "Content-Type": "application/json",
                            "X-Labbook-CSRF-Token": session.csrf_token,
                            "Origin": remote_origin,
                            "Host": remote_host,
                        },
                        method="POST",
                    )
                    response = urlrequest.urlopen(req, timeout=5)
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(payload.get("ok"))
                finally:
                    session.stop()


class ToolHandlerHardTimeoutTests(unittest.TestCase):
    def test_start_binding_server_hard_timeout_returns_error(self) -> None:
        import threading

        release = threading.Event()

        def _hang(args):
            # Block until the test releases us. The hard timeout in
            # handle_call_tool must return an error response long before this
            # wait completes, proving the MCP transport is not held hostage by
            # a slow handler. The daemon thread is allowed to keep running in
            # production, matching how start_binding_server leaves its HTTP
            # server alive after returning.
            release.wait(timeout=10.0)
            return {}

        async def _drive() -> tuple:
            started = time.monotonic()
            result = await handle_call_tool("notion_start_binding_server", {})
            return result, time.monotonic() - started

        loop = asyncio.new_event_loop()
        try:
            with mock.patch("labbook.server.get_handler", return_value=_hang):
                with mock.patch.dict(
                    "labbook.server._TOOL_HARD_TIMEOUT_SECONDS",
                    {"notion_start_binding_server": 0.3},
                    clear=False,
                ):
                    result, elapsed = loop.run_until_complete(_drive())
        finally:
            # Release the stuck worker thread before tearing down the loop so
            # loop.close() does not block on its executor join.
            release.set()
            # Give the executor a moment to reap the thread.
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()

        self.assertLess(
            elapsed,
            1.5,
            "handler hard timeout must short-circuit well before stdio read timeout",
        )
        self.assertTrue(result.isError)
        text = result.content[0].text
        self.assertIn("did not complete", text)
        self.assertIn("notion_start_binding_server", text)


if __name__ == "__main__":
    unittest.main()
