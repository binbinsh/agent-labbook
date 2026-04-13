from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import parse

logger = logging.getLogger("labbook.binding_ui")

from .auth_flow import open_browser_url, notion_client_for_project, status
from .binding_browser_page import render_binding_browser_page
from .binding_ops import (
    bind_resource_urls,
    bind_resources,
    build_discover_children_payload,
    build_search_resources_payload,
    list_bindings,
)
from .state import LabbookError, resolve_project_root


DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS = 1800
DEFAULT_BINDING_BROWSER_PAGE_SIZE = 25


@dataclass
class BindingBrowserSession:
    session_id: str
    project_root: str
    chooser_url: str
    browser_opened: bool
    open_browser_attempted: bool
    timeout_seconds: int
    page_size: int
    workspace_name: str | None
    binding_recommendation: dict[str, Any] | None
    binding_options: list[dict[str, Any]]
    binding_question: str | None
    _server: ThreadingHTTPServer
    _thread: threading.Thread
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _timer: threading.Timer | None = None
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)
    _search_cache: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    _children_cache: dict[tuple[str, str, int], dict[str, Any]] = field(
        default_factory=dict
    )

    def payload(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "chooser_url": self.chooser_url,
            "browser_opened": self.browser_opened,
            "open_browser_attempted": self.open_browser_attempted,
            "timeout_seconds": self.timeout_seconds,
            "page_size": self.page_size,
            "workspace_name": self.workspace_name,
            "binding_recommendation": self.binding_recommendation,
            "binding_options": self.binding_options,
            "binding_question": self.binding_question,
            "recommended_next_action": "Use the local browser chooser to select roots, then verify the result with notion_list_bindings.",
            "headless_flow_hint": "In headless environments, use notion_bind_resource_urls when the user can paste exact Notion links. Otherwise combine notion_search_resources, notion_discover_children, and notion_bind_resources.",
        }

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        logger.info("Binding browser session %s stopping", self.session_id)
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._timer is not None:
            self._timer.cancel()

    def wait(self) -> None:
        self._stop_event.wait()


def _json_error(
    handler: BaseHTTPRequestHandler, message: str, status_code: int
) -> None:
    payload = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _json_response(
    handler: BaseHTTPRequestHandler, payload: dict[str, Any], status_code: int = 200
) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    raw = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(content_length) if content_length else b""
    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LabbookError(f"Invalid JSON request body: {exc}") from exc
    if not isinstance(decoded, dict):
        raise LabbookError("Expected a JSON object.")
    return decoded


def _serve_root(
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
) -> None:
    _html_response(
        handler,
        render_binding_browser_page(
            {
                "project_root": session.project_root,
                "page_size": session.page_size,
                "workspace_name": session.workspace_name,
                "binding_recommendation": session.binding_recommendation,
                "binding_options": session.binding_options,
                "binding_question": session.binding_question,
                "session_id": session.session_id,
            }
        ),
    )


def _serve_search(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    query: dict[str, list[str]],
) -> None:
    search_query = str((query.get("query") or [""])[0]).strip()
    page_size = int((query.get("page_size") or [session.page_size])[0])
    cache_key = (search_query, page_size)
    with session._cache_lock:
        cached = session._search_cache.get(cache_key)
    if cached is not None:
        _json_response(handler, cached)
        return
    client, context = notion_client_for_project(session.project_root)
    payload = build_search_resources_payload(
        client,
        project_root=str(context["project_root"]),
        query=search_query or None,
        page_size=page_size,
    )
    with session._cache_lock:
        session._search_cache[cache_key] = payload
    _json_response(
        handler,
        payload,
    )


def _serve_children(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    query: dict[str, list[str]],
) -> None:
    resource_ref = (query.get("resource_id_or_url") or [None])[0]
    if not resource_ref:
        raise LabbookError("resource_id_or_url is required.")
    mode = str((query.get("mode") or ["shallow"])[0]).strip().lower() or "shallow"
    limit = int((query.get("limit") or [DEFAULT_BINDING_BROWSER_PAGE_SIZE])[0])
    cache_key = (str(resource_ref), mode, limit)
    with session._cache_lock:
        cached = session._children_cache.get(cache_key)
    if cached is not None:
        _json_response(handler, cached)
        return
    client, context = notion_client_for_project(session.project_root)
    payload = build_discover_children_payload(
        client,
        project_root=str(context["project_root"]),
        resource_id_or_url=str(resource_ref),
        resource_type=(query.get("resource_type") or [None])[0],
        limit=limit,
        mode=mode,
    )
    with session._cache_lock:
        session._children_cache[cache_key] = payload
    _json_response(
        handler,
        payload,
    )


def _serve_bind(
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
) -> None:
    payload = _read_json_body(handler)
    resource_refs = payload.get("resource_refs")
    if not isinstance(resource_refs, list):
        raise LabbookError("resource_refs must be a JSON array.")
    _json_response(
        handler,
        bind_resources(
            project_root=session.project_root,
            resource_refs=resource_refs,
            default_alias=payload.get("default_alias"),
        ),
    )


def _serve_bind_urls(
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
) -> None:
    payload = _read_json_body(handler)
    resource_urls = payload.get("resource_urls")
    if not isinstance(resource_urls, list):
        raise LabbookError("resource_urls must be a JSON array.")
    _json_response(
        handler,
        bind_resource_urls(
            project_root=session.project_root,
            resource_urls=[str(item) for item in resource_urls],
            selection_scope=payload.get("selection_scope") or "subtree",
            default_alias=payload.get("default_alias"),
        ),
    )


def _binding_browser_handler(session: BindingBrowserSession):
    class BindingBrowserHandler(BaseHTTPRequestHandler):
        server_version = "AgentLabbookBindingBrowser/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _validate_origin(self) -> bool:
            """Reject cross-origin mutating requests (CSRF protection)."""
            origin = self.headers.get("Origin") or ""
            if not origin:
                return True
            allowed = session.chooser_url.rstrip("/")
            if origin.rstrip("/") == allowed:
                return True
            _json_error(self, "Cross-origin request rejected.", 403)
            return False

        def do_GET(self) -> None:  # noqa: N802
            parsed = parse.urlparse(self.path)
            query = parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    _serve_root(self, session)
                    return
                if parsed.path == "/api/status":
                    _json_response(self, status(project_root=session.project_root))
                    return
                if parsed.path == "/api/search":
                    _serve_search(self, session, query)
                    return
                if parsed.path == "/api/children":
                    _serve_children(self, session, query)
                    return
                if parsed.path == "/api/bindings":
                    _json_response(
                        self, list_bindings(project_root=session.project_root)
                    )
                    return
                _json_error(self, "Not found.", 404)
            except LabbookError as exc:
                _json_error(self, str(exc), 400)
            except Exception as exc:  # noqa: BLE001
                _json_error(self, f"Unexpected error: {exc}", 500)

        def do_POST(self) -> None:  # noqa: N802
            if not self._validate_origin():
                return
            parsed = parse.urlparse(self.path)
            try:
                if parsed.path == "/api/bind":
                    _serve_bind(self, session)
                    return
                if parsed.path == "/api/bind-urls":
                    _serve_bind_urls(self, session)
                    return
                if parsed.path == "/api/shutdown":
                    _json_response(self, {"ok": True})
                    threading.Thread(target=session.stop, daemon=True).start()
                    return
                _json_error(self, "Not found.", 404)
            except LabbookError as exc:
                _json_error(self, str(exc), 400)
            except Exception as exc:  # noqa: BLE001
                _json_error(self, f"Unexpected error: {exc}", 500)

    return BindingBrowserHandler


def start_binding_browser(
    *,
    project_root: str | None = None,
    open_browser: bool = True,
    timeout_seconds: int = DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS,
    page_size: int = DEFAULT_BINDING_BROWSER_PAGE_SIZE,
    host: str = "127.0.0.1",
) -> BindingBrowserSession:
    root = resolve_project_root(project_root)
    status_payload = status(root)
    if not status_payload.get("authenticated"):
        raise LabbookError(
            "This project is not authenticated yet. Configure the Internal Integration secret before opening the binding chooser."
        )

    server = ThreadingHTTPServer((host, 0), BaseHTTPRequestHandler)
    session = BindingBrowserSession(
        session_id=f"chooser-{time.time_ns()}",
        project_root=str(root),
        chooser_url="",
        browser_opened=False,
        open_browser_attempted=bool(open_browser),
        timeout_seconds=max(30, int(timeout_seconds)),
        page_size=page_size,
        workspace_name=status_payload.get("workspace_name"),
        binding_recommendation=status_payload.get("binding_recommendation"),
        binding_options=list(status_payload.get("binding_options") or []),
        binding_question=status_payload.get("binding_question"),
        _server=server,
        _thread=threading.Thread(target=server.serve_forever, daemon=True),
    )
    server.RequestHandlerClass = _binding_browser_handler(session)
    port = int(server.server_address[1])
    session.chooser_url = f"http://{host}:{port}/"

    session._thread.start()
    session._timer = threading.Timer(session.timeout_seconds, session.stop)
    session._timer.daemon = True
    session._timer.start()
    session.browser_opened = (
        open_browser_url(session.chooser_url) if open_browser else False
    )
    logger.info(
        "Binding browser started at %s (session=%s, browser_opened=%s)",
        session.chooser_url,
        session.session_id,
        session.browser_opened,
    )
    return session
