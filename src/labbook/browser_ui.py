"""Browser UI: launch policy, binding chooser HTTP server, and HTML template rendering."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import parse

from .state import LabbookError, resolve_project_root

logger = logging.getLogger("labbook.browser_ui")

DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS = 1800
DEFAULT_BINDING_BROWSER_PAGE_SIZE = 25
DEFAULT_BROWSER_OPEN_TIMEOUT_SECONDS = 2.0
_HEADLESS_ENV_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "CI")
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "binding_chooser.html"
_PLACEHOLDER = "/*__LABBOOK_CONFIG_JSON__*/null"
_cached_template: str | None = None

BrowserOpener = Callable[[str], bool]


def likely_headless_environment(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else os.environ
    return any(str(values.get(k) or "").strip() for k in _HEADLESS_ENV_VARS)


def _system_browser_opener(url: str) -> bool:
    return bool(webbrowser.open(url, new=2))


@dataclass(frozen=True, slots=True)
class BrowserLaunchResult:
    attempted: bool
    opened: bool


@dataclass(frozen=True, slots=True)
class BrowserLaunchPolicy:
    should_attempt: bool
    timeout_seconds: float = DEFAULT_BROWSER_OPEN_TIMEOUT_SECONDS

    @classmethod
    def from_preference(
        cls,
        open_browser: bool | None,
        *,
        likely_headless: bool | None = None,
        timeout_seconds: float = DEFAULT_BROWSER_OPEN_TIMEOUT_SECONDS,
    ) -> BrowserLaunchPolicy:
        if likely_headless is None:
            likely_headless = likely_headless_environment()
        should_attempt = (
            bool(open_browser)
            if open_browser is not None
            else not bool(likely_headless)
        )
        return cls(
            should_attempt=should_attempt,
            timeout_seconds=max(0.0, float(timeout_seconds)),
        )

    def launch(
        self, url: str, *, opener: BrowserOpener | None = None
    ) -> BrowserLaunchResult:
        if not self.should_attempt:
            return BrowserLaunchResult(attempted=False, opened=False)
        completed = threading.Event()
        result = {"opened": False}
        resolved_opener = opener or _system_browser_opener

        def _open() -> None:
            try:
                result["opened"] = bool(resolved_opener(url))
            except Exception:
                result["opened"] = False
            finally:
                completed.set()

        threading.Thread(target=_open, name="labbook-open-browser", daemon=True).start()
        completed.wait(self.timeout_seconds)
        if not completed.is_set():
            return BrowserLaunchResult(attempted=True, opened=False)
        return BrowserLaunchResult(attempted=True, opened=bool(result["opened"]))


# ---------------------------------------------------------------------------
# HTML template rendering
# ---------------------------------------------------------------------------


def _load_template() -> str:
    global _cached_template
    if _cached_template is None:
        _cached_template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return _cached_template


def _inline_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_binding_browser_page(payload: dict[str, Any]) -> str:
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

    Used when ``start_binding_browser`` binds a wildcard interface on a headless
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
class BindingBrowserSession:
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
            "browser_opened": self.browser_opened,
            "open_browser_attempted": self.open_browser_attempted,
            "timeout_seconds": self.timeout_seconds,
            "page_size": self.page_size,
            "workspace_name": self.workspace_name,
            "binding_recommendation": self.binding_recommendation,
            "binding_options": self.binding_options,
            "binding_question": self.binding_question,
            "security_notes": list(self.security_notes),
            "recommended_next_action": "Use the browser chooser to select roots, then verify the result with notion_list_bindings.",
            "headless_flow_hint": "In headless environments, share one of the lan_urls with the user so they can open the chooser from their local browser without SSH port-forwarding. Otherwise fall back to notion_bind_resource_urls or notion_search_resources + notion_discover_children.",
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
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
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
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
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
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
) -> str:
    fp = _normalized_prefix(
        _first_forwarded_value(handler.headers.get("X-Forwarded-Prefix"))
    )
    return fp if fp else session._route_prefix


def _request_base_url(
    handler: BaseHTTPRequestHandler, session: BindingBrowserSession
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
    [BaseHTTPRequestHandler, BindingBrowserSession, dict[str, list[str]]], None
]


def _serve_root(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    base_url = _request_base_url(handler, session)
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
                "chooser_url": base_url,
                "api_base_url": base_url,
                "csrf_token": session.csrf_token,
            }
        ),
    )


def _serve_status(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    from .auth import status

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
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    from .notion import list_bindings

    _json_response(handler, list_bindings(project_root=session.project_root))


def _serve_bind(
    handler: BaseHTTPRequestHandler,
    session: BindingBrowserSession,
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
    session: BindingBrowserSession,
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
    session: BindingBrowserSession,
    _query: dict[str, list[str]],
) -> None:
    _json_response(handler, {"ok": True})
    threading.Thread(target=session.stop, daemon=True).start()


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
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentLabbookBindingBrowser/1.0"

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


def start_binding_browser(
    *,
    project_root: str | None = None,
    open_browser: bool | None = None,
    timeout_seconds: int = DEFAULT_BINDING_BROWSER_TIMEOUT_SECONDS,
    page_size: int = DEFAULT_BINDING_BROWSER_PAGE_SIZE,
    host: str | None = None,
    port: int = 0,
    public_base_url: str | None = None,
    allowed_origins: list[str] | None = None,
) -> BindingBrowserSession:
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
    launch_policy = BrowserLaunchPolicy.from_preference(
        open_browser,
        likely_headless=likely_headless,
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
    session = BindingBrowserSession(
        session_id=f"chooser-{time.time_ns()}",
        project_root=str(root),
        chooser_url=chooser_url,
        local_url=local_url,
        public_base_url=chooser_url if public_base_url else None,
        bind_host=bind_host,
        bind_port=actual_port,
        browser_opened=False,
        open_browser_attempted=launch_policy.should_attempt,
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
    server.RequestHandlerClass = _binding_browser_handler(session)
    session._thread.start()
    session._timer = threading.Timer(session.timeout_seconds, session.stop)
    session._timer.daemon = True
    session._timer.start()
    launch_result = launch_policy.launch(session.chooser_url)
    session.open_browser_attempted = launch_result.attempted
    session.browser_opened = launch_result.opened
    return session
