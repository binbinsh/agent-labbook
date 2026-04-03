from __future__ import annotations

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import threading
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from uuid import uuid4
import webbrowser

from .notion_api import NOTION_API_BASE, NotionClient
from . import __version__
from .state import (
    DEFAULT_NOTION_VERSION,
    backend_redirect_uri,
    bindings_path,
    clear_pending_auth,
    clear_project_bindings,
    clear_project_session,
    effective_backend_url,
    LabbookError,
    load_project_bindings,
    load_project_session,
    load_pending_auth,
    normalize_notion_id,
    pending_auth_path,
    resolve_project_root,
    save_project_bindings,
    save_project_session,
    save_pending_auth,
    session_path,
)


LOCAL_CALLBACK_HOST = "127.0.0.1"
LOCAL_CALLBACK_PORT = 8765
LOCAL_CALLBACK_PATH = "/oauth/handoff"
CLIENT_USER_AGENT = f"AgentLabbook/{__version__} (+https://github.com/binbinsh/agent-labbook)"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rich_text_to_plain_text(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    text = "".join(str(item.get("plain_text") or "") for item in items if isinstance(item, dict)).strip()
    return text or None


def _resource_title(resource: dict[str, Any]) -> str | None:
    title = _rich_text_to_plain_text(resource.get("title"))
    if title:
        return title

    properties = resource.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if not isinstance(prop, dict):
                continue
            title_items = prop.get("title")
            title = _rich_text_to_plain_text(title_items)
            if title:
                return title
            if prop.get("type") == "title":
                title = _rich_text_to_plain_text(prop.get("title"))
                if title:
                    return title

    url = str(resource.get("url") or "").strip()
    resource_id = str(resource.get("id") or "").strip()
    return url or resource_id or None


def _slugify_alias(text: str, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug or fallback


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    if not resources:
        return None
    return str(resources[0].get("alias") or "").strip() or None


def _padding(value: str) -> str:
    return "=" * ((4 - len(value) % 4) % 4)


def _decode_handoff_bundle(handoff_bundle: str) -> dict[str, Any]:
    raw_bundle = str(handoff_bundle or "").strip()
    if not raw_bundle:
        raise LabbookError("handoff_bundle cannot be empty.")

    if raw_bundle.startswith("{"):
        payload = json.loads(raw_bundle)
    else:
        try:
            decoded = base64.urlsafe_b64decode(raw_bundle + _padding(raw_bundle)).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise LabbookError("handoff_bundle is not valid base64url JSON.") from exc
        payload = json.loads(decoded)

    if not isinstance(payload, dict):
        raise LabbookError("handoff_bundle must decode to an object.")
    return payload


def _resolve_handoff_bundle(
    *,
    backend_url: str,
    expected_session_id: str,
    handoff_bundle: str,
) -> dict[str, Any]:
    payload = _post_backend_json(
        f"{backend_url}/api/consume-handoff",
        {
            "session_id": expected_session_id,
            "handoff_bundle": handoff_bundle,
        },
    )
    if not payload.get("ok"):
        raise LabbookError(str(payload.get("error") or "Worker could not validate the handoff bundle."))
    decoded = payload.get("payload")
    if not isinstance(decoded, dict):
        raise LabbookError("Worker handoff validation did not return a payload.")
    return decoded


def _endpoint_for_resource(resource_type: str | None, resource_id: str) -> str | None:
    normalized_type = str(resource_type or "").strip()
    if normalized_type == "page":
        return f"{NOTION_API_BASE}/pages/{resource_id}"
    if normalized_type in {"data_source", "database"}:
        return f"{NOTION_API_BASE}/data_sources/{resource_id}"
    return None


def _normalize_binding_entry(
    *,
    resource_id: str,
    resource_type: str,
    title: str | None,
    resource_url: str | None,
    alias: str | None,
    source: str,
    bound_at: str,
) -> dict[str, Any]:
    clean_id = normalize_notion_id(resource_id)
    clean_type = str(resource_type or "unknown").strip() or "unknown"
    clean_title = str(title or "").strip() or f"Notion resource {clean_id[:8]}"
    clean_alias = str(alias or "").strip() or _slugify_alias(clean_title, fallback=f"resource-{clean_id[:8]}")
    return {
        "alias": clean_alias,
        "resource_id": clean_id,
        "resource_type": clean_type,
        "resource_url": str(resource_url or "").strip() or None,
        "title": clean_title,
        "source": source,
        "bound_at": bound_at,
    }


def _bindings_payload(
    *,
    resources: list[dict[str, Any]],
    project_root: Path,
    default_alias: str | None = None,
) -> dict[str, Any]:
    chosen_default = default_alias or _default_resource_alias(resources)
    if chosen_default:
        aliases = {str(resource.get("alias") or "").strip() for resource in resources}
        if chosen_default not in aliases:
            raise LabbookError(f"default_alias {chosen_default!r} was not found in the final bindings.")
    return {
        "version": 1,
        "project_root": str(project_root),
        "updated_at": _utc_now(),
        "default_resource_alias": chosen_default,
        "resources": resources,
    }


def _build_setup_guide() -> str:
    backend_url = effective_backend_url()
    return "\n".join(
        [
            "# Agent Labbook Public Integration Setup",
            "",
            "For MCP users:",
            "1. Call `notion_auth_browser` for a direct browser flow, or `notion_start_headless_auth` if the browser cannot be opened locally.",
            "2. Complete the official Notion public integration consent page.",
            "3. On the Labbook handoff page, choose the pages or data sources that should be bound to this project.",
            "4. Call `notion_get_api_context` and use the official Notion API directly with the returned access token.",
            "",
            "For backend maintainers:",
            "1. Create a Notion Public integration named `Agent Labbook`.",
            f"2. Add this redirect URI in the Notion integration settings: {backend_redirect_uri(backend_url)}",
            "3. Deploy the Cloudflare Worker with Wrangler.",
            "4. Set the Worker secrets `NOTION_CLIENT_ID` and `NOTION_CLIENT_SECRET`.",
            "",
            "Privacy:",
            f"- The hosted service at {backend_url} is privacy-friendly. It only handles OAuth and token refresh.",
            "- Long-lived tokens and project bindings stay in `.labbook/` inside the current project.",
        ]
    )


def _notion_client(session_payload: dict[str, Any]) -> NotionClient:
    access_token = str(session_payload.get("access_token") or "").strip()
    if not access_token:
        raise LabbookError("No Notion access token is configured for this project.")
    return NotionClient(token=access_token)


def _normalize_resource_input(item: dict[str, Any]) -> dict[str, str | None]:
    if not isinstance(item, dict):
        raise LabbookError("Each binding input must be an object.")
    raw_ref = item.get("resource_id_or_url") or item.get("resource_id") or item.get("resource_url")
    resource_ref = str(raw_ref or "").strip()
    if not resource_ref:
        raise LabbookError("Each binding requires resource_id_or_url, resource_id, or resource_url.")
    return {
        "resource_id": normalize_notion_id(resource_ref),
        "resource_type": str(item.get("resource_type") or "").strip() or None,
        "alias": str(item.get("alias") or "").strip() or None,
    }


def _merge_bindings(
    *,
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]],
    default_alias: str | None,
    project_root: Path,
) -> dict[str, Any]:
    merged = [resource for resource in list(existing or []) if isinstance(resource, dict)]
    for resource in incoming:
        resource_id = str(resource.get("resource_id") or "").strip()
        alias = str(resource.get("alias") or "").strip()
        merged = [
            current
            for current in merged
            if str(current.get("resource_id") or "").strip() != resource_id
            and str(current.get("alias") or "").strip() != alias
        ]
        merged.append(resource)
    return _bindings_payload(resources=merged, project_root=project_root, default_alias=default_alias)


def _post_backend_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": CLIENT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise LabbookError(f"Backend returned HTTP {exc.code}: {raw or exc.reason}") from exc
    except error.URLError as exc:
        raise LabbookError(f"Could not reach the Worker backend: {exc.reason}") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LabbookError("Worker backend returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise LabbookError("Worker backend returned an unexpected payload.")
    return decoded


class _LocalHandoffServer:
    def __init__(self, *, expected_session_id: str) -> None:
        self.expected_session_id = expected_session_id
        self._queue: queue.Queue[str] = queue.Queue(maxsize=1)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def return_to_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Server has not started yet.")
        host, port = self._server.server_address
        return f"http://{host}:{port}{LOCAL_CALLBACK_PATH}"

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

            def do_POST(self) -> None:  # noqa: N802
                outer._handle_post(self)

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Agent Labbook local handoff endpoint.\n")

        try:
            self._server = ThreadingHTTPServer((LOCAL_CALLBACK_HOST, LOCAL_CALLBACK_PORT), Handler)
        except OSError:
            self._server = ThreadingHTTPServer((LOCAL_CALLBACK_HOST, 0), Handler)

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def wait_for_bundle(self, timeout_seconds: int) -> str:
        try:
            return self._queue.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise LabbookError("Timed out waiting for the local browser handoff to complete.") from exc

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

        try:
            self._queue.put_nowait(bundle)
        except queue.Full:
            pass

        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(
            (
                "<!doctype html><html><body>"
                "<h1>Agent Labbook is connected</h1>"
                "<p>You can close this tab and return to Codex.</p>"
                "</body></html>"
            ).encode("utf-8")
        )


def _save_session_payload(project_root: Path, *, backend_url: str, token_payload: dict[str, Any]) -> Path:
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise LabbookError("The OAuth handoff did not contain both access_token and refresh_token.")

    payload = {
        "version": 1,
        "project_root": str(project_root),
        "backend_url": backend_url,
        "authorized_at": _utc_now(),
        "last_refreshed_at": _utc_now(),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(token_payload.get("token_type") or "bearer").strip() or "bearer",
        "bot_id": str(token_payload.get("bot_id") or "").strip() or None,
        "workspace_id": str(token_payload.get("workspace_id") or "").strip() or None,
        "workspace_name": str(token_payload.get("workspace_name") or "").strip() or None,
        "workspace_icon": str(token_payload.get("workspace_icon") or "").strip() or None,
        "duplicated_template_id": str(token_payload.get("duplicated_template_id") or "").strip() or None,
        "owner": token_payload.get("owner") if isinstance(token_payload.get("owner"), dict) else None,
    }
    return save_project_session(project_root, payload)


def _bindings_from_selected_resources(
    *,
    selected_resources: list[dict[str, Any]],
    project_root: Path,
    default_alias: str | None = None,
) -> dict[str, Any]:
    bound_at = _utc_now()
    alias_counts: dict[str, int] = {}
    resources: list[dict[str, Any]] = []
    for item in selected_resources:
        resource_id = normalize_notion_id(str(item.get("resource_id") or ""))
        title = str(item.get("title") or "").strip() or f"Notion resource {resource_id[:8]}"
        base_alias = _slugify_alias(title, fallback=f"resource-{resource_id[:8]}")
        alias_index = alias_counts.get(base_alias, 0)
        alias_counts[base_alias] = alias_index + 1
        alias = base_alias if alias_index == 0 else f"{base_alias}-{alias_index + 1}"
        resources.append(
            _normalize_binding_entry(
                resource_id=resource_id,
                resource_type=str(item.get("resource_type") or "unknown").strip() or "unknown",
                title=title,
                resource_url=str(item.get("resource_url") or "").strip() or None,
                alias=alias,
                source="oauth_selection",
                bound_at=bound_at,
            )
        )
    return _bindings_payload(resources=resources, project_root=project_root, default_alias=default_alias)


def _complete_auth_handoff(
    *,
    project_root: Path,
    pending_auth: dict[str, Any],
    handoff_bundle: str,
) -> dict[str, Any]:
    expected_session_id = str(pending_auth.get("session_id") or "").strip()
    backend_url = str(pending_auth.get("backend_url") or effective_backend_url()).strip()
    decoded = _resolve_handoff_bundle(
        backend_url=backend_url,
        expected_session_id=expected_session_id,
        handoff_bundle=handoff_bundle,
    )
    session_id = str(decoded.get("session_id") or "").strip()
    if not session_id or session_id != expected_session_id:
        raise LabbookError("The OAuth handoff session_id did not match the pending auth request.")
    token_payload = decoded.get("token")
    if not isinstance(token_payload, dict):
        raise LabbookError("The OAuth handoff did not contain token details.")

    selected_resources_raw = decoded.get("selected_resources")
    selected_resources = [item for item in list(selected_resources_raw or []) if isinstance(item, dict)]

    session_file = _save_session_payload(project_root, backend_url=backend_url, token_payload=token_payload)
    bindings_payload = _bindings_from_selected_resources(
        selected_resources=selected_resources,
        project_root=project_root,
    )
    binding_file = save_project_bindings(project_root, bindings_payload)
    clear_pending_auth(project_root)

    return {
        "project_root": str(project_root),
        "backend_url": backend_url,
        "auth_mode": pending_auth.get("mode"),
        "workspace_name": str(token_payload.get("workspace_name") or "").strip() or None,
        "workspace_id": str(token_payload.get("workspace_id") or "").strip() or None,
        "session_path": str(session_file),
        "binding_path": str(binding_file),
        "default_resource_alias": bindings_payload.get("default_resource_alias"),
        "resources": list(bindings_payload.get("resources") or []),
    }


def _pending_auth_payload(
    *,
    project_root: Path,
    backend_url: str,
    mode: str,
    session_id: str,
    auth_url: str,
    return_to: str | None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(project_root),
        "backend_url": backend_url,
        "mode": mode,
        "session_id": session_id,
        "auth_url": auth_url,
        "return_to": return_to,
        "started_at": _utc_now(),
    }


def _oauth_start_url(
    *,
    backend_url: str,
    project_root: Path,
    session_id: str,
    mode: str,
    return_to: str | None = None,
    page_limit: int = 50,
) -> str:
    query = {
        "mode": mode,
        "session_id": session_id,
        "project_name": project_root.name,
        "page_limit": str(page_limit),
    }
    if return_to:
        query["return_to"] = return_to
    return f"{backend_url}/oauth/start?{parse.urlencode(query)}"


def setup_guide() -> str:
    return _build_setup_guide()


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    backend_url = effective_backend_url()
    session_payload = load_project_session(root) or {}
    pending_auth = load_pending_auth(root) or {}
    bindings = load_project_bindings(root) or {}
    resources = list(bindings.get("resources") or [])
    authenticated = bool(str(session_payload.get("access_token") or "").strip())
    bindings_ready = bool(resources)

    if pending_auth and str(pending_auth.get("mode") or "") == "headless" and not authenticated:
        recommended_action = "notion_complete_headless_auth"
    elif not authenticated:
        recommended_action = "notion_auth_browser"
    elif not bindings_ready:
        recommended_action = "notion_bind_resources"
    else:
        recommended_action = "notion_get_api_context"

    return {
        "project_root": str(root),
        "backend_url": backend_url,
        "redirect_uri": backend_redirect_uri(backend_url),
        "auth_modes": ["local_browser", "headless"],
        "authenticated": authenticated,
        "refresh_supported": bool(str(session_payload.get("refresh_token") or "").strip()),
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "bound_resource_count": len(resources),
        "default_resource_alias": bindings.get("default_resource_alias"),
        "resources": resources,
        "session_path": str(session_path(root)),
        "binding_path": str(bindings_path(root)),
        "pending_auth_path": str(pending_auth_path(root)),
        "pending_auth": pending_auth or None,
        "ready": authenticated and bindings_ready,
        "recommended_action": recommended_action,
        "setup_guide": None if authenticated else _build_setup_guide(),
    }


def auth_browser(
    *,
    project_root: str | Path | None = None,
    timeout_seconds: int = 300,
    open_browser: bool = True,
    page_limit: int = 500,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    backend_url = effective_backend_url()
    session_id = uuid4().hex

    handoff_server = _LocalHandoffServer(expected_session_id=session_id)
    handoff_server.start()
    auth_url = _oauth_start_url(
        backend_url=backend_url,
        project_root=root,
        session_id=session_id,
        mode="local_browser",
        return_to=handoff_server.return_to_url,
        page_limit=page_limit,
    )
    save_pending_auth(
        root,
        _pending_auth_payload(
            project_root=root,
            backend_url=backend_url,
            mode="local_browser",
            session_id=session_id,
            auth_url=auth_url,
            return_to=handoff_server.return_to_url,
        ),
    )

    try:
        opened = webbrowser.open(auth_url) if open_browser else False
        handoff_bundle = handoff_server.wait_for_bundle(timeout_seconds)
        pending_auth = load_pending_auth(root) or {}
        result = _complete_auth_handoff(
            project_root=root,
            pending_auth=pending_auth,
            handoff_bundle=handoff_bundle,
        )
        result["auth_url"] = auth_url
        result["browser_opened"] = bool(opened)
        return result
    except Exception:
        clear_pending_auth(root)
        raise
    finally:
        handoff_server.close()


def start_headless_auth(
    *,
    project_root: str | Path | None = None,
    page_limit: int = 500,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    backend_url = effective_backend_url()
    session_id = uuid4().hex
    auth_url = _oauth_start_url(
        backend_url=backend_url,
        project_root=root,
        session_id=session_id,
        mode="headless",
        page_limit=page_limit,
    )
    save_pending_auth(
        root,
        _pending_auth_payload(
            project_root=root,
            backend_url=backend_url,
            mode="headless",
            session_id=session_id,
            auth_url=auth_url,
            return_to=None,
        ),
    )
    return {
        "project_root": str(root),
        "backend_url": backend_url,
        "auth_url": auth_url,
        "session_id": session_id,
        "instructions": (
            "Open auth_url in any browser, finish the Notion consent screen, choose project bindings on the "
            "Labbook page, then paste the resulting handoff bundle into notion_complete_headless_auth."
        ),
    }


def complete_headless_auth(
    *,
    project_root: str | Path | None = None,
    handoff_bundle: str,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    pending_auth = load_pending_auth(root) or {}
    if not pending_auth:
        raise LabbookError("No pending headless auth session was found for this project.")
    if str(pending_auth.get("mode") or "") != "headless":
        raise LabbookError("The pending auth session for this project is not a headless flow.")
    return _complete_auth_handoff(
        project_root=root,
        pending_auth=pending_auth,
        handoff_bundle=handoff_bundle,
    )


def refresh_session(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root) or {}
    refresh_token = str(session_payload.get("refresh_token") or "").strip()
    if not refresh_token:
        raise LabbookError("No refresh token is available for this project.")

    backend_url = str(session_payload.get("backend_url") or effective_backend_url()).strip()
    payload = _post_backend_json(f"{backend_url}/api/refresh", {"refresh_token": refresh_token})
    if not payload.get("ok"):
        raise LabbookError(str(payload.get("error") or "Worker refresh failed."))
    token_payload = payload.get("token")
    if not isinstance(token_payload, dict):
        raise LabbookError("Worker refresh response did not contain a token payload.")

    _save_session_payload(root, backend_url=backend_url, token_payload=token_payload)
    return {
        "project_root": str(root),
        "backend_url": backend_url,
        "workspace_name": token_payload.get("workspace_name"),
        "workspace_id": token_payload.get("workspace_id"),
        "session_path": str(session_path(root)),
        "refreshed": True,
    }


def clear_project_auth(
    *,
    project_root: str | Path | None = None,
    clear_bindings: bool = False,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    cleared_session = clear_project_session(root)
    cleared_pending = clear_pending_auth(root)
    cleared_bindings = clear_project_bindings(root) if clear_bindings else False
    return {
        "project_root": str(root),
        "cleared_session": cleared_session,
        "cleared_pending_auth": cleared_pending,
        "cleared_bindings": cleared_bindings,
    }


def bind_resources(
    *,
    project_root: str | Path | None = None,
    resource_refs: list[dict[str, Any]],
    default_alias: str | None = None,
) -> dict[str, Any]:
    if not resource_refs:
        raise LabbookError("resource_refs cannot be empty.")
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root) or {}
    client = _notion_client(session_payload)
    normalized_refs = [_normalize_resource_input(item) for item in resource_refs]
    existing_bindings = load_project_bindings(root) or {"resources": []}

    bound_at = _utc_now()
    alias_counts: dict[str, int] = {
        str(resource.get("alias") or "").strip(): 1
        for resource in list(existing_bindings.get("resources") or [])
        if isinstance(resource, dict)
    }
    new_resources: list[dict[str, Any]] = []
    for item in normalized_refs:
        resource = client.retrieve_resource(str(item["resource_id"]), str(item["resource_type"] or ""))
        resource_id = normalize_notion_id(str(resource.get("id") or item["resource_id"]))
        title = _resource_title(resource) or f"Notion resource {resource_id[:8]}"
        base_alias = str(item["alias"] or _slugify_alias(title, fallback=f"resource-{resource_id[:8]}")).strip()
        alias_index = alias_counts.get(base_alias, 0)
        alias_counts[base_alias] = alias_index + 1
        alias = base_alias if alias_index == 0 else f"{base_alias}-{alias_index + 1}"
        new_resources.append(
            _normalize_binding_entry(
                resource_id=resource_id,
                resource_type=str(resource.get("object") or item.get("resource_type") or "unknown"),
                title=title,
                resource_url=str(resource.get("url") or "").strip() or None,
                alias=alias,
                source="manual_bind",
                bound_at=bound_at,
            )
        )

    payload = _merge_bindings(
        existing=list(existing_bindings.get("resources") or []),
        incoming=new_resources,
        default_alias=default_alias,
        project_root=root,
    )
    path = save_project_bindings(root, payload)
    return {
        "project_root": str(root),
        "binding_path": str(path),
        "default_resource_alias": payload.get("default_resource_alias"),
        "resources": payload["resources"],
    }


def list_bindings(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    payload = load_project_bindings(root) or {
        "version": 1,
        "project_root": str(root),
        "default_resource_alias": None,
        "resources": [],
    }
    return {
        "project_root": str(root),
        "binding_path": str(bindings_path(root)),
        "default_resource_alias": payload.get("default_resource_alias"),
        "resources": list(payload.get("resources") or []),
    }


def get_api_context(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root) or {}
    access_token = str(session_payload.get("access_token") or "").strip()
    if not access_token:
        raise LabbookError("No Notion public integration access token is configured. Run `notion_auth_browser` first.")

    bindings = load_project_bindings(root) or {"resources": [], "default_resource_alias": None}
    resources = list(bindings.get("resources") or [])
    default_alias = str(bindings.get("default_resource_alias") or "").strip() or None
    default_binding = next((resource for resource in resources if resource.get("alias") == default_alias), None)
    if default_binding is None and resources:
        default_binding = resources[0]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": DEFAULT_NOTION_VERSION,
        "Content-Type": "application/json",
    }

    default_resource_id = str(default_binding.get("resource_id") or "").strip() if default_binding else ""
    default_resource_type = str(default_binding.get("resource_type") or "").strip() if default_binding else ""
    curl_example = None
    endpoint = _endpoint_for_resource(default_resource_type, default_resource_id)
    if endpoint:
        curl_example = (
            "curl -sS "
            f"-H 'Authorization: Bearer {access_token}' "
            f"-H 'Notion-Version: {DEFAULT_NOTION_VERSION}' "
            f"'{endpoint}'"
        )

    return {
        "project_root": str(root),
        "api_base": NOTION_API_BASE,
        "notion_version": DEFAULT_NOTION_VERSION,
        "docs_reference": "https://developers.notion.com/reference/intro",
        "docs_versioning": "https://developers.notion.com/reference/versioning",
        "access_token": access_token,
        "headers": headers,
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "default_resource_alias": default_alias,
        "default_binding": default_binding,
        "resources": resources,
        "binding_path": str(bindings_path(root)),
        "session_path": str(session_path(root)),
        "refresh_supported": True,
        "refresh_tool": "notion_refresh_session",
        "usage": "Use the official Notion REST API directly with this public integration access token and these bound resources. If the endpoint shape is uncertain, check the latest Notion API reference first.",
        "curl_example": curl_example,
    }
