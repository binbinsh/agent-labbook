from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib import parse

from .state import (
    clear_local_handoff_server,
    load_local_handoff_server,
    resolve_project_root,
    save_local_handoff_server,
    save_pending_handoff,
)


LOCAL_CALLBACK_HOST = "127.0.0.1"
LOCAL_CALLBACK_PORT = 8765
LOCAL_CALLBACK_PATH = "/oauth/handoff"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _PersistentLocalHandoffServer:
    def __init__(self, *, project_root: Path, expected_session_id: str, timeout_seconds: int) -> None:
        self.project_root = project_root
        self.expected_session_id = expected_session_id
        self.timeout_seconds = timeout_seconds
        self._received = False

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Agent Labbook local handoff endpoint.\n")

            def do_POST(self) -> None:  # noqa: N802
                outer._handle_post(self)

        try:
            self._server = ThreadingHTTPServer((LOCAL_CALLBACK_HOST, LOCAL_CALLBACK_PORT), Handler)
        except OSError:
            self._server = ThreadingHTTPServer((LOCAL_CALLBACK_HOST, 0), Handler)
        self._server.timeout = 0.5

    @property
    def return_to_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}{LOCAL_CALLBACK_PATH}"

    def _save_server_state(self) -> None:
        host, port = self._server.server_address
        save_local_handoff_server(
            self.project_root,
            {
                "version": 1,
                "project_root": str(self.project_root),
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "return_to": self.return_to_url,
                "session_id": self.expected_session_id,
                "started_at": _utc_now(),
                "timeout_seconds": self.timeout_seconds,
            },
        )

    def _clear_server_state(self) -> None:
        current = load_local_handoff_server(self.project_root) or {}
        current_pid = int(current.get("pid") or 0)
        current_session_id = str(current.get("session_id") or "").strip()
        if current_pid == os.getpid() and current_session_id == self.expected_session_id:
            clear_local_handoff_server(self.project_root)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if parse.urlsplit(handler.path).path != LOCAL_CALLBACK_PATH:
            handler.send_response(404)
            handler.end_headers()
            return

        length = int(handler.headers.get("Content-Length") or "0")
        raw_body = handler.rfile.read(length).decode("utf-8", errors="replace")
        content_type = str(handler.headers.get("Content-Type") or "")

        if "application/json" in content_type:
            decoded = json.loads(raw_body or "{}")
            bundle = str(decoded.get("handoff_bundle") or "").strip()
            session_id = str(decoded.get("session_id") or "").strip()
        else:
            form = parse.parse_qs(raw_body, keep_blank_values=False)
            bundle = str((form.get("handoff_bundle") or [""])[0]).strip()
            session_id = str((form.get("session_id") or [""])[0]).strip()

        if session_id and session_id != self.expected_session_id:
            handler.send_response(400)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(b"<h1>Session mismatch</h1><p>Please restart the auth flow.</p>")
            return
        if not bundle:
            handler.send_response(400)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(b"<h1>Missing bundle</h1><p>Please restart the auth flow.</p>")
            return

        save_pending_handoff(
            self.project_root,
            {
                "version": 1,
                "project_root": str(self.project_root),
                "session_id": self.expected_session_id,
                "handoff_bundle": bundle,
                "received_at": _utc_now(),
                "return_to": self.return_to_url,
            },
        )
        self._received = True

        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        message_payload = json.dumps(
            {
                "type": "agent-labbook-local-handoff-success",
                "session_id": self.expected_session_id,
            }
        )
        handler.wfile.write(
            (
                "<!doctype html><html><body>"
                "<h1>Agent Labbook is connected</h1>"
                "<p>You can close this tab and return to Codex.</p>"
                "<script>"
                f"const payload = {message_payload};"
                "if (window.opener && !window.opener.closed) {"
                "  try { window.opener.postMessage(payload, '*'); } catch {}"
                "}"
                "</script>"
                "</body></html>"
            ).encode("utf-8")
        )

    def run(self) -> int:
        self._save_server_state()
        try:
            deadline = datetime.now(timezone.utc).timestamp() + self.timeout_seconds
            while not self._received and datetime.now(timezone.utc).timestamp() < deadline:
                self._server.handle_request()
            return 0
        finally:
            self._server.server_close()
            self._clear_server_state()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m labbook.local_handoff")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    server = _PersistentLocalHandoffServer(
        project_root=resolve_project_root(args.project_root),
        expected_session_id=str(args.session_id).strip(),
        timeout_seconds=max(int(args.timeout_seconds), 30),
    )
    return server.run()


if __name__ == "__main__":
    raise SystemExit(main())
