from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import parse

from .auth_flow import notion_client_for_project, open_browser_url, status
from .binding_browser_page import render_binding_browser_page
from .binding_ops import (
    bind_resource_urls,
    bind_resources,
    build_discover_children_payload,
    build_search_resources_payload,
    list_bindings,
)
from .state import LabbookError, resolve_project_root

logger = logging.getLogger("labbook.binding_ui")

__all__ = [
    "BindingBrowserSession",
    "start_binding_browser",
    "DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS",
    "DEFAULT_BINDING_BROWSER_PAGE_SIZE",
]

DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS = 1800
DEFAULT_BINDING_BROWSER_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class BindingBrowserSession:
    """Tracks a running binding-browser HTTP session."""

    session_id: str
    project_root: str
    chooser_url: str
    local_url: str
    public_base_url: str | None
    bind_host: str
    bind_port: int
    browser_opened: bool
    open_browser_attempted: bool
    timeout_seconds: int
    page_size: int
    workspace_name: str | None
    binding_recommendation: dict[str, Any] | None
    binding_options: list[dict[str, Any]]
    binding_question: str | None
    csrf_token: str = field(repr=False)
    _server: ThreadingHTTPServer
    _thread: threading.Thread
    _route_prefix: str = ""
    _allowed_origins: set[str] = field(default_factory=set, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _timer: threading.Timer | None = None
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)
    _search_cache: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    _children_cache: dict[tuple[str, str, int], dict[str, Any]] = field(
        default_factory=dict
    )

    def payload(self) -> dict[str, Any]:
        """Return the session state as a serializable dict for tool output."""
        return {
            "project_root": self.project_root,
            "chooser_url": self.chooser_url,
            "local_url": self.local_url,
            "public_base_url": self.public_base_url,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "browser_opened": self.browser_opened,
            "open_browser_attempted": self.open_browser_attempted,
            "timeout_seconds": self.timeout_seconds,
            "page_size": self.page_size,
            "workspace_name": self.workspace_name,
            "binding_recommendation": self.binding_recommendation,
            "binding_options": self.binding_options,
            "binding_question": self.binding_question,
            "recommended_next_action": (
                "Use the browser chooser to select roots, "
                "then verify the result with notion_list_bindings."
            ),
            "headless_flow_hint": (
                "In headless environments, use notion_bind_resource_urls "
                "when the user can paste exact Notion links. Otherwise combine "
                "notion_search_resources, notion_discover_children, "
                "and notion_bind_resources."
            ),
        }

    def stop(self) -> None:
        """Shut down the HTTP server and cancel the timeout timer."""
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
        """Block until the session is stopped."""
        self._stop_event.wait()


# ---------------------------------------------------------------------------
# HTTP response helpers
# ---------------------------------------------------------------------------


def _send(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    *,
    content_type: str,
    status_code: int = 200,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Write a complete HTTP response."""
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    status_code: int = 200,
) -> None:
    _send(
        handler,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        status_code=status_code,
    )


def _json_error(
    handler: BaseHTTPRequestHandler, message: str, status_code: int
) -> None:
    _json_response(handler, {"error": message}, status_code)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    _send(
        handler,
        html.encode("utf-8"),
        content_type="text/html; charset=utf-8",
        extra_headers={"Cache-Control": "no-store"},
    )


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


# ---------------------------------------------------------------------------
# Query-string helpers
# ---------------------------------------------------------------------------


def _qs_str(query: dict[str, list[str]], key: str, default: str = "") -> str:
    """Return the first query-string value for *key*, or *default*."""
    values = query.get(key)
    return str(values[0]) if values else default


def _qs_int(query: dict[str, list[str]], key: str, default: int) -> int:
    """Return the first query-string value for *key* as int, or *default*."""
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (ValueError, TypeError):
        return default


def _is_loopback_host(hostname: str | None) -> bool:
    clean = str(hostname or "").strip().lower()
    return clean in {"127.0.0.1", "localhost", "::1"}


def _normalize_base_url(url: str) -> str:
    parsed = parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise LabbookError("public_base_url must be an absolute URL.")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = path + "/"
    return parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _default_port_for_scheme(scheme: str | None) -> int | None:
    clean = str(scheme or "").strip().lower()
    if clean == "http":
        return 80
    if clean == "https":
        return 443
    return None


def _normalized_origin(url: str | None) -> tuple[str, str, int] | None:
    parsed = parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return None
    port = parsed.port or _default_port_for_scheme(parsed.scheme)
    if port is None:
        return None
    return (parsed.scheme.lower(), parsed.hostname.lower(), int(port))


def _same_origin(left: str, right: str) -> bool:
    left_origin = _normalized_origin(left)
    right_origin = _normalized_origin(right)
    if left_origin is None or right_origin is None:
        return False
    if left_origin[0] != right_origin[0] or left_origin[2] != right_origin[2]:
        return False
    if left_origin[1] == right_origin[1]:
        return True
    return _is_loopback_host(left_origin[1]) and _is_loopback_host(right_origin[1])


def _route_prefix_from_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = parse.urlparse(url)
    path = str(parsed.path or "").rstrip("/")
    if not path or path == "/":
        return ""
    return path if path.startswith("/") else f"/{path}"


def _strip_route_prefix(path: str, prefix: str) -> str:
    clean_path = str(path or "") or "/"
    if not prefix:
        return clean_path
    if clean_path == prefix:
        return "/"
    if clean_path.startswith(prefix + "/"):
        return clean_path[len(prefix) :] or "/"
    return clean_path


def _display_host_for_bind_host(host: str) -> str:
    clean = str(host or "").strip()
    return "127.0.0.1" if clean in {"0.0.0.0", "::", ""} else clean


# ---------------------------------------------------------------------------
# GET route handlers
# ---------------------------------------------------------------------------


def _serve_root(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
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
                "chooser_url": session.chooser_url,
                "api_base_url": session.chooser_url,
                "csrf_token": session.csrf_token,
            }
        ),
    )


def _serve_status(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    _json_response(handler, status(project_root=session.project_root))


def _serve_search(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    query: dict[str, list[str]],
) -> None:
    search_query = _qs_str(query, "query").strip()
    fetch_all = _qs_str(query, "fetch_all").lower() in ("1", "true", "yes")
    page_size = _qs_int(query, "page_size", session.page_size)
    cache_key = (search_query, page_size, fetch_all)

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
        fetch_all=fetch_all,
    )
    with session._cache_lock:
        session._search_cache[cache_key] = payload
    _json_response(handler, payload)


def _serve_children(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    query: dict[str, list[str]],
) -> None:
    resource_ref = _qs_str(query, "resource_id_or_url")
    if not resource_ref:
        raise LabbookError("resource_id_or_url is required.")
    mode = _qs_str(query, "mode", "shallow").lower() or "shallow"
    limit = _qs_int(query, "limit", 200)
    cache_key = (resource_ref, mode, limit)

    with session._cache_lock:
        cached = session._children_cache.get(cache_key)
    if cached is not None:
        _json_response(handler, cached)
        return

    client, context = notion_client_for_project(session.project_root)
    payload = build_discover_children_payload(
        client,
        project_root=str(context["project_root"]),
        resource_id_or_url=resource_ref,
        resource_type=_qs_str(query, "resource_type") or None,
        limit=limit,
        mode=mode,
    )
    with session._cache_lock:
        session._children_cache[cache_key] = payload
    _json_response(handler, payload)


def _serve_bindings(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    _json_response(handler, list_bindings(project_root=session.project_root))


# ---------------------------------------------------------------------------
# POST route handlers
# ---------------------------------------------------------------------------


def _serve_bind(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    body = _read_json_body(handler)
    resource_refs = body.get("resource_refs")
    if not isinstance(resource_refs, list):
        raise LabbookError("resource_refs must be a JSON array.")
    _json_response(
        handler,
        bind_resources(
            project_root=session.project_root,
            resource_refs=resource_refs,
            default_alias=body.get("default_alias"),
        ),
    )


def _serve_bind_urls(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    body = _read_json_body(handler)
    resource_urls = body.get("resource_urls")
    if not isinstance(resource_urls, list):
        raise LabbookError("resource_urls must be a JSON array.")
    _json_response(
        handler,
        bind_resource_urls(
            project_root=session.project_root,
            resource_urls=[str(item) for item in resource_urls],
            selection_scope=body.get("selection_scope") or "subtree",
            default_alias=body.get("default_alias"),
        ),
    )


def _serve_shutdown(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    _json_response(handler, {"ok": True})
    threading.Thread(target=session.stop, daemon=True).start()


# ---------------------------------------------------------------------------
# Route tables
# ---------------------------------------------------------------------------

_RouteHandler = Callable[
    [BaseHTTPRequestHandler, BindingBrowserSession, dict[str, list[str]]],
    None,
]

_GET_ROUTES: dict[str, _RouteHandler] = {
    "/": _serve_root,
    "/api/status": _serve_status,
    "/api/search": _serve_search,
    "/api/children": _serve_children,
    "/api/bindings": _serve_bindings,
}

_POST_ROUTES: dict[str, _RouteHandler] = {
    "/api/bind": _serve_bind,
    "/api/bind-urls": _serve_bind_urls,
    "/api/shutdown": _serve_shutdown,
}


# ---------------------------------------------------------------------------
# Request handler factory
# ---------------------------------------------------------------------------


def _binding_browser_handler(session: BindingBrowserSession):
    """Return a BaseHTTPRequestHandler subclass bound to *session*."""

    class BindingBrowserHandler(BaseHTTPRequestHandler):
        server_version = "AgentLabbookBindingBrowser/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _validate_origin(self) -> bool:
            """Reject cross-origin mutating requests (CSRF protection)."""
            csrf_token = self.headers.get("X-Labbook-CSRF-Token") or ""
            if csrf_token != session.csrf_token:
                _json_error(self, "Invalid or missing CSRF token.", 403)
                return False

            origin = self.headers.get("Origin") or ""
            if not origin:
                return True
            if any(
                _same_origin(origin.rstrip("/"), allowed)
                for allowed in session._allowed_origins
            ):
                return True
            if origin == "null":
                referer = self.headers.get("Referer") or ""
                if referer and any(
                    _same_origin(referer.rstrip("/"), allowed)
                    for allowed in session._allowed_origins
                ):
                    return True
                _json_error(self, "Cross-origin request rejected.", 403)
                return False
            _json_error(self, "Cross-origin request rejected.", 403)
            return False

        def _dispatch(
            self,
            routes: dict[str, _RouteHandler],
        ) -> None:
            parsed = parse.urlparse(self.path)
            route_path = _strip_route_prefix(parsed.path, session._route_prefix)
            query = parse.parse_qs(parsed.query)
            route_fn = routes.get(route_path)
            if route_fn is None:
                _json_error(self, "Not found.", 404)
                return
            try:
                route_fn(self, session, query)
            except LabbookError as exc:
                _json_error(self, str(exc), 400)
            except Exception as exc:  # noqa: BLE001
                _json_error(self, f"Unexpected error: {exc}", 500)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(_GET_ROUTES)

        def do_POST(self) -> None:  # noqa: N802
            if not self._validate_origin():
                return
            self._dispatch(_POST_ROUTES)

    return BindingBrowserHandler


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def start_binding_browser(
    *,
    project_root: str | None = None,
    open_browser: bool = True,
    timeout_seconds: int = DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS,
    page_size: int = DEFAULT_BINDING_BROWSER_PAGE_SIZE,
    host: str = "127.0.0.1",
    port: int = 0,
    public_base_url: str | None = None,
    allowed_origins: list[str] | None = None,
) -> BindingBrowserSession:
    """Start a local HTTP server for the binding browser UI.

    Returns a :class:`BindingBrowserSession` with the running server.
    Call :meth:`BindingBrowserSession.stop` to tear it down.
    """
    root = resolve_project_root(project_root)
    status_payload = status(root)
    if not status_payload.get("authenticated"):
        raise LabbookError(
            "This project is not authenticated yet. "
            "Configure the Internal Integration secret before "
            "opening the binding chooser."
        )

    bind_host = str(host or "127.0.0.1").strip() or "127.0.0.1"
    bind_port = max(0, int(port or 0))
    server = ThreadingHTTPServer((bind_host, bind_port), BaseHTTPRequestHandler)
    actual_port = int(server.server_address[1])
    local_url = (
        f"http://{_display_host_for_bind_host(bind_host)}:{actual_port}/"
    )
    chooser_url = _normalize_base_url(public_base_url) if public_base_url else local_url
    route_prefix = _route_prefix_from_url(public_base_url)
    normalized_allowed_origins: set[str] = set()
    for candidate in [chooser_url, local_url, *(allowed_origins or [])]:
        if candidate:
            normalized_allowed_origins.add(str(candidate).rstrip("/"))

    session = BindingBrowserSession(
        session_id=f"chooser-{time.time_ns()}",
        project_root=str(root),
        chooser_url=chooser_url,
        local_url=local_url,
        public_base_url=chooser_url if public_base_url else None,
        bind_host=bind_host,
        bind_port=actual_port,
        browser_opened=False,
        open_browser_attempted=bool(open_browser),
        timeout_seconds=max(30, int(timeout_seconds)),
        page_size=page_size,
        workspace_name=status_payload.get("workspace_name"),
        binding_recommendation=status_payload.get("binding_recommendation"),
        binding_options=list(status_payload.get("binding_options") or []),
        binding_question=status_payload.get("binding_question"),
        csrf_token=secrets.token_urlsafe(32),
        _server=server,
        _thread=threading.Thread(target=server.serve_forever, daemon=True),
        _route_prefix=route_prefix,
        _allowed_origins=normalized_allowed_origins,
    )
    server.RequestHandlerClass = _binding_browser_handler(session)

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
