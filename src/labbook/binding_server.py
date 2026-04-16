"""Binding chooser HTTP server and HTML template rendering.

This module is intentionally free of ``webbrowser.open`` and any other call
that could spawn a GUI browser subprocess. The MCP stdio server shares stdout
with the Python process, and subprocesses such as ``xdg-open``/``firefox``/
``google-chrome`` inherit that file descriptor by default and routinely emit
banners or warnings that corrupt JSON-RPC frames, causing the MCP client to
drop the transport (observed symptom: ``Transport closed`` after calling the
binding tool on headless SSH hosts). We therefore never attempt to open a
system browser from inside the MCP server process; the agent hands the user
one of the URLs returned in the payload and the user opens it themselves.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import parse

from .state import LabbookError, resolve_project_root

logger = logging.getLogger("labbook.binding_server")

DEFAULT_BINDING_SERVER_TIMEOUT_SECONDS = 1800
DEFAULT_BINDING_SERVER_PAGE_SIZE = 25
_HEADLESS_ENV_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "CI")
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_PATH = _TEMPLATE_DIR / "binding_chooser.html"
_CHOOSER_APP_PATH = _TEMPLATE_DIR / "binding_chooser_app.js"
_PLACEHOLDER = "/*__LABBOOK_CONFIG_JSON__*/null"
_cached_template: str | None = None
_cached_chooser_app_script: str | None = None


def likely_headless_environment(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else os.environ
    return any(str(values.get(k) or "").strip() for k in _HEADLESS_ENV_VARS)


# ---------------------------------------------------------------------------
# HTML template rendering
# ---------------------------------------------------------------------------


def _load_template() -> str:
    global _cached_template
    if _cached_template is None:
        _cached_template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return _cached_template


def _load_chooser_app_script() -> str:
    global _cached_chooser_app_script
    if _cached_chooser_app_script is None:
        _cached_chooser_app_script = _CHOOSER_APP_PATH.read_text(encoding="utf-8")
    return _cached_chooser_app_script


def _inline_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_chooser_page(payload: dict[str, Any]) -> str:
    return _load_template().replace(_PLACEHOLDER, _inline_json(payload), 1)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _send(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    *,
    content_type: str,
    status_code: int = 200,
    extra_headers: dict[str, str] | None = None,
) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for k, v in (extra_headers or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _json_response(
    handler: BaseHTTPRequestHandler, payload: dict[str, Any], status_code: int = 200
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


def _qs_str(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return str(values[0]) if values else default


def _qs_int(query: dict[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Origin / URL helpers
# ---------------------------------------------------------------------------


def _is_loopback_host(hostname: str | None) -> bool:
    return str(hostname or "").strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _normalize_base_url(url: str) -> str:
    parsed = parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise LabbookError("public_base_url must be an absolute URL.")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _default_port_for_scheme(scheme: str | None) -> int | None:
    return {"http": 80, "https": 443}.get(str(scheme or "").strip().lower())


def _normalized_origin(url: str | None) -> tuple[str, str, int] | None:
    parsed = parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return None
    port = parsed.port or _default_port_for_scheme(parsed.scheme)
    if port is None:
        return None
    return (parsed.scheme.lower(), parsed.hostname.lower(), int(port))


def _same_origin(left: str, right: str) -> bool:
    lo, ro = _normalized_origin(left), _normalized_origin(right)
    if lo is None or ro is None:
        return False
    if lo[0] != ro[0] or lo[2] != ro[2]:
        return False
    if lo[1] == ro[1]:
        return True
    return _is_loopback_host(lo[1]) and _is_loopback_host(ro[1])


def _route_prefix_from_url(url: str | None) -> str:
    if not url:
        return ""
    path = str(parse.urlparse(url).path or "").rstrip("/")
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


def _is_wildcard_bind_host(host: str) -> bool:
    return str(host or "").strip() in {"0.0.0.0", "::", ""}


def _enumerate_lan_hosts() -> list[str]:
    """Return non-loopback/link-local IPv4 addresses attached to this host.

    Used when ``start_binding_server`` binds a wildcard interface on a headless
    machine so the payload can tell the agent (and the user) concrete URLs they
    can open from an SSH client without port-forwarding. Best-effort: any
    failure returns an empty list.
    """

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
    except OSError:
        infos = []
    hosts: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw = str(sockaddr[0] or "").strip()
        if not raw or raw in seen:
            continue
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            continue
        seen.add(raw)
        hosts.append(raw)
    return hosts


def _build_lan_urls(port: int, route_prefix: str) -> list[str]:
    suffix = (route_prefix + "/") if route_prefix else "/"
    return [f"http://{host}:{port}{suffix}" for host in _enumerate_lan_hosts()]


def _first_forwarded_value(raw: str | None) -> str | None:
    clean = str(raw or "").strip()
    if not clean:
        return None
    return clean.split(",", 1)[0].strip() or None


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class BindingServerSession:
    session_id: str
    project_root: str
    chooser_url: str
    local_url: str
    public_base_url: str | None
    bind_host: str
    bind_port: int
    timeout_seconds: int
    page_size: int
    workspace_name: str | None
    binding_recommendation: dict[str, Any] | None
    binding_options: list[dict[str, Any]]
    binding_question: str | None
    csrf_token: str = field(repr=False)
    _server: ThreadingHTTPServer = field(repr=False)
    _thread: threading.Thread = field(repr=False)
    _route_prefix: str = ""
    _allowed_origins: set[str] = field(default_factory=set, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _timer: threading.Timer | None = None
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)
    _search_cache: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    _children_cache: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    lan_urls: list[str] = field(default_factory=list)
    wildcard_bind: bool = False
    security_notes: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "chooser_url": self.chooser_url,
            "local_url": self.local_url,
            "public_base_url": self.public_base_url,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "wildcard_bind": self.wildcard_bind,
            "lan_urls": list(self.lan_urls),
            "timeout_seconds": self.timeout_seconds,
            "page_size": self.page_size,
            "workspace_name": self.workspace_name,
            "binding_recommendation": self.binding_recommendation,
            "binding_options": self.binding_options,
            "binding_question": self.binding_question,
            "security_notes": list(self.security_notes),
            "recommended_next_action": (
                "Share one of the chooser URLs with the user so they can open it in "
                "their own browser, then verify the result with notion_list_bindings."
            ),
            "headless_flow_hint": (
                "This tool never launches a browser. On headless hosts share one of "
                "lan_urls; on desktop hosts share local_url. If the user cannot open "
                "a browser, fall back to notion_bind_resource_urls or "
                "notion_search_resources + notion_discover_children."
            ),
        }

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
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


# ---------------------------------------------------------------------------
# Request context helpers
# ---------------------------------------------------------------------------


def _request_scheme(
    handler: BaseHTTPRequestHandler, session: BindingServerSession
) -> str:
    fp = _first_forwarded_value(handler.headers.get("X-Forwarded-Proto"))
    if fp:
        return fp
    if session.public_base_url:
        parsed = parse.urlparse(session.public_base_url)
        if parsed.scheme:
            return parsed.scheme
    return "http"


def _request_netloc(
    handler: BaseHTTPRequestHandler, session: BindingServerSession
) -> str:
    fh = _first_forwarded_value(handler.headers.get("X-Forwarded-Host"))
    if fh:
        return fh
    host_header = str(handler.headers.get("Host") or "").strip()
    if host_header:
        return host_header
    parsed = parse.urlparse(session.chooser_url)
    if parsed.netloc:
        return parsed.netloc
    return f"{_display_host_for_bind_host(session.bind_host)}:{session.bind_port}"


def _normalized_prefix(prefix: str | None) -> str:
    clean = str(prefix or "").strip()
    if not clean:
        return ""
    if not clean.startswith("/"):
        clean = f"/{clean}"
    return clean.rstrip("/")


def _request_route_prefix(
    handler: BaseHTTPRequestHandler, session: BindingServerSession
) -> str:
    fp = _normalized_prefix(
        _first_forwarded_value(handler.headers.get("X-Forwarded-Prefix"))
    )
    return fp if fp else session._route_prefix


def _request_base_url(
    handler: BaseHTTPRequestHandler, session: BindingServerSession
) -> str:
    scheme = _request_scheme(handler, session)
    netloc = _request_netloc(handler, session)
    prefix = _request_route_prefix(handler, session)
    path = (prefix + "/") if prefix else "/"
    return parse.urlunparse((scheme, netloc, path, "", "", ""))


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

_RouteHandler = Callable[
    [BaseHTTPRequestHandler, BindingServerSession, dict[str, list[str]]], None
]


def _serve_root(
    handler: BaseHTTPRequestHandler,
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    base_url = _request_base_url(handler, session)
    _html_response(
        handler,
        render_chooser_page(
            {
                "project_root": session.project_root,
                "page_size": session.page_size,
                "workspace_name": session.workspace_name,
                "binding_recommendation": session.binding_recommendation,
                "binding_options": session.binding_options,
                "binding_question": session.binding_question,
                "session_id": session.session_id,
                "chooser_url": base_url,
                "api_base_url": base_url,
                "csrf_token": session.csrf_token,
            }
        ),
    )


def _serve_chooser_app_script(
    handler: BaseHTTPRequestHandler,
    _session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    _send(
        handler,
        _load_chooser_app_script().encode("utf-8"),
        content_type="text/javascript; charset=utf-8",
        extra_headers={"Cache-Control": "no-store"},
    )


def _serve_status(
    handler: BaseHTTPRequestHandler,
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    from .auth import status

    _json_response(handler, status(project_root=session.project_root))


def _serve_search(
    handler: BaseHTTPRequestHandler,
    session: BindingServerSession,
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
    from .auth import notion_client_for_project
    from .notion import build_search_resources_payload

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
    session: BindingServerSession,
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
    from .auth import notion_client_for_project
    from .notion import build_discover_children_payload

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
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    from .notion import list_bindings

    _json_response(handler, list_bindings(project_root=session.project_root))


def _serve_bind(
    handler: BaseHTTPRequestHandler,
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    from .notion import bind_resources

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
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    from .notion import bind_resource_urls

    body = _read_json_body(handler)
    resource_urls = body.get("resource_urls")
    if not isinstance(resource_urls, list):
        raise LabbookError("resource_urls must be a JSON array.")
    _json_response(
        handler,
        bind_resource_urls(
            project_root=session.project_root,
            resource_urls=[str(i) for i in resource_urls],
            selection_scope=body.get("selection_scope") or "subtree",
            default_alias=body.get("default_alias"),
        ),
    )


def _serve_shutdown(
    handler: BaseHTTPRequestHandler,
    session: BindingServerSession,
    _query: dict[str, list[str]],
) -> None:
    _json_response(handler, {"ok": True})
    threading.Thread(target=session.stop, daemon=True).start()


_GET_ROUTES: dict[str, _RouteHandler] = {
    "/": _serve_root,
    "/assets/binding_chooser_app.js": _serve_chooser_app_script,
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


def _binding_server_handler(session: BindingServerSession):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentLabbookBindingServer/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _validate_origin(self) -> bool:
            csrf_token = self.headers.get("X-Labbook-CSRF-Token") or ""
            if csrf_token != session.csrf_token:
                _json_error(self, "Invalid or missing CSRF token.", 403)
                return False
            allowed_bases = set(session._allowed_origins) | {
                _request_base_url(self, session).rstrip("/")
            }
            origin = self.headers.get("Origin") or ""
            if not origin:
                return True
            if any(_same_origin(origin.rstrip("/"), a) for a in allowed_bases):
                return True
            if origin == "null":
                referer = self.headers.get("Referer") or ""
                if referer and any(
                    _same_origin(referer.rstrip("/"), a) for a in allowed_bases
                ):
                    return True
                _json_error(self, "Cross-origin request rejected.", 403)
                return False
            _json_error(self, "Cross-origin request rejected.", 403)
            return False

        def _dispatch(self, routes: dict[str, _RouteHandler]) -> None:
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
            except Exception as exc:
                _json_error(self, f"Unexpected error: {exc}", 500)

        def do_GET(self) -> None:
            self._dispatch(_GET_ROUTES)

        def do_POST(self) -> None:
            if not self._validate_origin():
                return
            self._dispatch(_POST_ROUTES)

    return Handler


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def start_binding_server(
    *,
    project_root: str | None = None,
    timeout_seconds: int = DEFAULT_BINDING_SERVER_TIMEOUT_SECONDS,
    page_size: int = DEFAULT_BINDING_SERVER_PAGE_SIZE,
    host: str | None = None,
    port: int = 0,
    public_base_url: str | None = None,
    allowed_origins: list[str] | None = None,
) -> BindingServerSession:
    """Start the local binding chooser HTTP server.

    This function never spawns a browser. Callers must share ``chooser_url``
    (or one of ``lan_urls`` for headless hosts) with the user so they can
    open it in their own browser.
    """

    from .auth import status

    root = resolve_project_root(project_root)
    status_payload = status(root)
    if not status_payload.get("authenticated"):
        raise LabbookError(
            "This project is not authenticated yet. Configure the Internal Integration secret before opening the binding chooser."
        )
    likely_headless_value = status_payload.get("likely_headless")
    likely_headless = (
        bool(likely_headless_value)
        if likely_headless_value is not None
        else likely_headless_environment()
    )
    # Bind host policy:
    #   - Explicit host argument wins (including "127.0.0.1" or "0.0.0.0").
    #   - Otherwise default to 0.0.0.0 on headless hosts so SSH clients can
    #     reach the chooser without port-forwarding, and 127.0.0.1 on desktops.
    host_was_explicit = host is not None
    raw_host = str(host).strip() if host is not None else ""
    if host_was_explicit and raw_host:
        bind_host = raw_host
    elif likely_headless:
        bind_host = "0.0.0.0"
    else:
        bind_host = "127.0.0.1"
    bind_port = max(0, int(port or 0))
    server = ThreadingHTTPServer((bind_host, bind_port), BaseHTTPRequestHandler)
    actual_port = int(server.server_address[1])
    local_url = f"http://{_display_host_for_bind_host(bind_host)}:{actual_port}/"
    chooser_url = _normalize_base_url(public_base_url) if public_base_url else local_url
    route_prefix = _route_prefix_from_url(public_base_url)
    wildcard_bind = _is_wildcard_bind_host(bind_host)
    lan_urls = _build_lan_urls(actual_port, route_prefix) if wildcard_bind else []
    security_notes: list[str] = []
    if wildcard_bind:
        security_notes.append(
            "Chooser is bound to a wildcard interface and is reachable from any host "
            "that can route to this machine on the LAN. Share lan_urls only on trusted "
            "networks."
        )
        security_notes.append(
            "Authentication uses a per-session CSRF token delivered via the "
            "X-Labbook-CSRF-Token request header; no browser cookies are set or "
            "required, so the token cannot leak across ports on the same host."
        )
    normalized_origins: set[str] = set()
    for candidate in [chooser_url, local_url, *(allowed_origins or [])]:
        if candidate:
            normalized_origins.add(str(candidate).rstrip("/"))
    # When we bind a wildcard interface without an explicit public_base_url, also
    # trust the LAN-reachable URLs so the agent can hand them to the user and the
    # browser's Origin header (e.g. "http://192.168.1.10:PORT") still passes the
    # same-origin check.
    if wildcard_bind and not public_base_url:
        for lan_url in lan_urls:
            normalized_origins.add(str(lan_url).rstrip("/"))
    session = BindingServerSession(
        session_id=f"chooser-{time.time_ns()}",
        project_root=str(root),
        chooser_url=chooser_url,
        local_url=local_url,
        public_base_url=chooser_url if public_base_url else None,
        bind_host=bind_host,
        bind_port=actual_port,
        timeout_seconds=max(30, int(timeout_seconds)),
        page_size=page_size,
        workspace_name=status_payload.get("workspace_name"),
        binding_recommendation=status_payload.get("binding_recommendation"),
        binding_options=list(status_payload.get("binding_options") or []),
        binding_question=status_payload.get("binding_question"),
        lan_urls=lan_urls,
        wildcard_bind=wildcard_bind,
        security_notes=security_notes,
        csrf_token=secrets.token_urlsafe(32),
        _server=server,
        _thread=threading.Thread(target=server.serve_forever, daemon=True),
        _route_prefix=route_prefix,
        _allowed_origins=normalized_origins,
    )
    server.RequestHandlerClass = _binding_server_handler(session)
    session._thread.start()
    session._timer = threading.Timer(session.timeout_seconds, session.stop)
    session._timer.daemon = True
    session._timer.start()
    return session
