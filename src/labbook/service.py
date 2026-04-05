from __future__ import annotations

from datetime import datetime, timezone
import importlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any
from urllib import error, parse, request
from uuid import uuid4
import webbrowser

from .notion_api import NOTION_API_BASE, NotionClient
from . import __version__
from .state import (
    DEFAULT_NOTION_VERSION,
    bindings_path,
    clear_pending_auth,
    clear_pending_handoff,
    clear_local_handoff_server,
    clear_project_bindings,
    clear_project_session,
    effective_backend_url,
    effective_oauth_base_url,
    LabbookError,
    load_local_handoff_server,
    load_pending_handoff,
    load_project_bindings,
    load_project_session,
    load_pending_auth,
    local_handoff_server_path,
    normalize_notion_id,
    oauth_callback_uri,
    pending_auth_path,
    pending_handoff_path,
    INTEGRATION_ID,
    resolve_project_root,
    save_project_bindings,
    save_project_session,
    save_pending_auth,
    session_path,
)


CLIENT_USER_AGENT = f"AgentLabbook/{__version__} (+https://github.com/binbinsh/agent-labbook)"
BROKER_API_VERSION = 1
SUPPORTED_BROKER_API_VERSIONS = (BROKER_API_VERSION,)
BROKER_API_VERSIONS_HEADER = ",".join(str(version) for version in SUPPORTED_BROKER_API_VERSIONS)
DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS = 1800
MIN_BROWSER_AUTH_TIMEOUT_SECONDS = 30
DEFAULT_BROWSER_AUTH_PAGE_LIMIT = 200
MIN_BROWSER_AUTH_PAGE_LIMIT = 25
LOCAL_HANDOFF_SERVER_STARTUP_TIMEOUT_SECONDS = 5
SETUP_GUIDE_RESOURCE_URI = "labbook://agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/status{?project_root}"
BINDINGS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/bindings{?project_root}"
NOTION_ACCESS_BROKER_SRC_ENV_VAR = "NOTION_ACCESS_BROKER_SRC"
FORCE_HEADLESS_ENV_VAR = "AGENT_LABBOOK_FORCE_HEADLESS"
FORCE_LOCAL_BROWSER_ENV_VAR = "AGENT_LABBOOK_FORCE_LOCAL_BROWSER"
_GRAPHICAL_BROWSER_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("xdg-open",),
    ("open",),
    ("gio", "open"),
)
_TEXT_BROWSER_NAMES = frozenset(
    {
        "www-browser",
        "lynx",
        "links",
        "links2",
        "elinks",
        "w3m",
        "eww",
        "browsh",
    }
)
_SSH_ENV_VARS: tuple[str, ...] = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
_DISPLAY_ENV_VARS: tuple[str, ...] = ("DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_utc_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_browser_auth_timeout_seconds(timeout_seconds: int | str | None = None) -> int:
    if timeout_seconds in (None, ""):
        return DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise LabbookError("timeout_seconds must be an integer number of seconds.") from exc
    if timeout < MIN_BROWSER_AUTH_TIMEOUT_SECONDS:
        raise LabbookError(
            f"timeout_seconds must be at least {MIN_BROWSER_AUTH_TIMEOUT_SECONDS} seconds."
        )
    return timeout


def normalize_browser_auth_page_limit(page_limit: int | str | None = None) -> int:
    if page_limit in (None, ""):
        return DEFAULT_BROWSER_AUTH_PAGE_LIMIT
    try:
        limit = int(page_limit)
    except (TypeError, ValueError) as exc:
        raise LabbookError("page_limit must be an integer number of resources.") from exc
    if limit < MIN_BROWSER_AUTH_PAGE_LIMIT:
        return MIN_BROWSER_AUTH_PAGE_LIMIT
    return min(limit, DEFAULT_BROWSER_AUTH_PAGE_LIMIT)


def _url_relay_hint(url_field_name: str) -> str:
    return (
        f"When relaying {url_field_name} to the user, print the raw URL exactly once on its own line. "
        "Do not repeat the same URL in markdown link syntax, angle brackets, or parentheses."
    )


def _parse_optional_bool_env(name: str) -> bool | None:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _browser_environment() -> dict[str, Any]:
    ssh_session_detected = any(str(os.environ.get(name) or "").strip() for name in _SSH_ENV_VARS)
    display_detected = any(str(os.environ.get(name) or "").strip() for name in _DISPLAY_ENV_VARS)
    graphical_launcher_available = any(shutil.which(parts[0]) for parts in _GRAPHICAL_BROWSER_COMMANDS)
    force_headless = bool(_parse_optional_bool_env(FORCE_HEADLESS_ENV_VAR))
    force_local_browser = bool(_parse_optional_bool_env(FORCE_LOCAL_BROWSER_ENV_VAR))

    preferred_browser_flow = "local_browser"
    recommended_open_browser = True
    override_source: str | None = None
    reason = "No remote-session warning was detected, so a same-machine browser flow is acceptable."

    if force_headless:
        preferred_browser_flow = "headless"
        recommended_open_browser = False
        override_source = FORCE_HEADLESS_ENV_VAR
        reason = (
            f"{FORCE_HEADLESS_ENV_VAR} is set, so browser-based localhost handoff flows are being forced into headless mode."
        )
    elif force_local_browser:
        preferred_browser_flow = "local_browser"
        recommended_open_browser = True
        override_source = FORCE_LOCAL_BROWSER_ENV_VAR
        reason = (
            f"{FORCE_LOCAL_BROWSER_ENV_VAR} is set, so browser-based localhost handoff flows are being treated as same-machine."
        )
    elif ssh_session_detected:
        preferred_browser_flow = "headless"
        recommended_open_browser = False
        reason = (
            "SSH session variables were detected. Local browser flows use a localhost callback, so unless the browser is truly "
            "running on this host and can reach its 127.0.0.1 listener, headless is safer."
        )
    elif os.name != "nt" and sys.platform != "darwin" and not display_detected:
        preferred_browser_flow = "headless"
        recommended_open_browser = False
        reason = "No graphical display session was detected in this environment, so headless is safer."
    elif os.name != "nt" and sys.platform != "darwin" and not graphical_launcher_available:
        preferred_browser_flow = "headless"
        recommended_open_browser = False
        reason = "No graphical browser launcher was detected in this environment, so headless is safer."

    return {
        "preferred_browser_flow": preferred_browser_flow,
        "recommended_open_browser": recommended_open_browser,
        "ssh_session_detected": ssh_session_detected,
        "display_detected": display_detected,
        "graphical_launcher_available": graphical_launcher_available,
        "override_source": override_source,
        "reason": reason,
    }


def _launch_detached_command(command: list[str]) -> bool:
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError:
        return False
    if process.poll() is None:
        return True
    return process.returncode == 0


def _is_text_browser_controller(controller: Any) -> bool:
    raw_name = str(getattr(controller, "name", "") or "").strip()
    if not raw_name:
        return False
    resolved_name = shutil.which(raw_name) or raw_name
    candidates = {
        Path(raw_name).name.lower(),
        Path(resolved_name).name.lower(),
    }
    return any(candidate in _TEXT_BROWSER_NAMES for candidate in candidates)


def _open_browser_url(url: str) -> bool:
    for launcher_parts in _GRAPHICAL_BROWSER_COMMANDS:
        launcher = shutil.which(launcher_parts[0])
        if not launcher:
            continue
        if _launch_detached_command([launcher, *launcher_parts[1:], url]):
            return True

    try:
        controller = webbrowser.get()
    except webbrowser.Error:
        return False

    if _is_text_browser_controller(controller):
        return False

    if isinstance(controller, webbrowser.GenericBrowser):
        raw_name = str(getattr(controller, "name", "") or "").strip()
        args = list(getattr(controller, "args", []) or [])
        if not raw_name:
            return False
        executable = shutil.which(raw_name) or raw_name
        command = [executable] + [str(arg).replace("%s", url) for arg in args]
        return _launch_detached_command(command)

    return bool(controller.open(url))


def pending_auth_is_stale(pending_auth: dict[str, Any] | None) -> bool:
    if not isinstance(pending_auth, dict) or not pending_auth:
        return False
    started_at = _parse_utc_timestamp(pending_auth.get("started_at"))
    if started_at is None:
        return False

    mode = str(pending_auth.get("mode") or "").strip()
    timeout_seconds = (
        normalize_browser_auth_timeout_seconds(pending_auth.get("timeout_seconds"))
        if mode == "local_browser"
        else 3600
    )
    age_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
    return age_seconds > timeout_seconds


def _clear_stale_pending_auth_if_needed(project_root: str | Path | None = None) -> bool:
    pending_auth = load_pending_auth(project_root) or {}
    if not pending_auth_is_stale(pending_auth):
        return False
    clear_pending_auth(project_root)
    _clear_local_browser_handoff_state(project_root, terminate_server=True)
    return True


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


def _normalize_selection_scope(value: Any) -> str:
    scope = str(value or "resource").strip().lower() or "resource"
    if scope not in {"resource", "subtree"}:
        raise LabbookError("selection_scope must be 'resource' or 'subtree'.")
    return scope


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    if not resources:
        return None
    return str(resources[0].get("alias") or "").strip() or None


def _resolve_handoff_bundle(
    *,
    oauth_base_url: str,
    expected_session_id: str,
    handoff_bundle: str,
) -> dict[str, Any]:
    payload = _post_backend_json(
        f"{oauth_base_url}/api/consume-handoff",
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
    selection_scope: str | None = None,
) -> dict[str, Any]:
    clean_id = normalize_notion_id(resource_id)
    clean_type = str(resource_type or "unknown").strip() or "unknown"
    clean_title = str(title or "").strip() or f"Notion resource {clean_id[:8]}"
    clean_alias = str(alias or "").strip() or _slugify_alias(clean_title, fallback=f"resource-{clean_id[:8]}")
    clean_selection_scope = str(selection_scope or "resource").strip() or "resource"
    return {
        "alias": clean_alias,
        "resource_id": clean_id,
        "resource_type": clean_type,
        "resource_url": str(resource_url or "").strip() or None,
        "title": clean_title,
        "source": source,
        "bound_at": bound_at,
        "selection_scope": clean_selection_scope,
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
    oauth_base_url = effective_oauth_base_url()
    return "\n".join(
        [
            "# Agent Labbook Public Integration Setup",
            "",
            "For MCP users:",
            "1. Call `notion_status` or read the `labbook://agent-labbook/project/status` resource first. If it reports saved shared credentials for this integration, prefer `notion_list_saved_credentials` and `notion_attach_saved_credential` before re-running OAuth.",
            "   If `saved_credentials_error` or `credential_provider_diagnostics_error` is non-null, fix the local notion-access-broker helper installation before re-running OAuth.",
            "   If your client supports ask-user-question style prompts, you can map `notion_status.connect_decision.questions` directly into those prompts.",
            "   If it does not, show `notion_status.connect_decision.manual_prompt_markdown` to the user and wait for a reply before choosing tools.",
            "   Use `notion_selection_browser` only when the integration's current Notion access scope already includes the pages or databases you want and you only need to choose project bindings.",
            "   If you need the official Notion root-page chooser again to expand what the integration can access, start a fresh OAuth flow instead.",
            "2. If you still need OAuth, start with `notion_auth_browser` if you want the simplest flow. It starts a localhost handoff listener and returns immediately so the browser flow can finish asynchronously.",
            f"   For browser auth, pass `timeout_seconds: {DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS}` or longer so the background localhost listener stays available while you finish consent and resource selection.",
            "   After the browser says the project is connected, call `notion_status`. If `pending_handoff_ready` is true, call `notion_finalize_pending_auth` to persist the session and bindings. If the browser handoff cannot get back to the MCP server, use `notion_complete_headless_auth` with the handoff bundle shown on the page.",
            f"   `page_limit` now controls the size of the initial recent-items catalog. Values below {MIN_BROWSER_AUTH_PAGE_LIMIT} are clamped, but remote search still searches the whole shared workspace.",
            "3. Complete the official Notion public integration consent page.",
            "4. On the Labbook handoff page, choose the pages or data sources that should be bound to this project.",
            "5. Call `notion_get_api_context` and use the official Notion API directly with the returned access token.",
            "",
            "For backend maintainers:",
            f"1. Deploy or reuse a shared Notion OAuth service such as {oauth_base_url}.",
            f"2. Add this redirect URI in the Notion integration settings: {oauth_callback_uri(oauth_base_url)}",
            f"3. Configure Agent Labbook to use the shared OAuth service with `AGENT_LABBOOK_OAUTH_BASE_URL={oauth_base_url}`.",
            "4. Configure shared local credential storage through the notion-access-broker Python helpers.",
            "   If the local `op` CLI is available and can access 1Password, the default provider automatically prefers",
            "   `1password`. Set `NOTION_ACCESS_BROKER_1PASSWORD_VAULT=YOUR_VAULT` only if you want to pin a specific",
            "   vault; otherwise the default 1Password vault is used. If 1Password is unavailable, it falls back to `keyring`.",
            f"5. Deploy the Agent Labbook app Worker at {backend_url}.",
            "",
            "Privacy:",
            f"- The hosted OAuth service at {oauth_base_url} is privacy-friendly. It only handles OAuth and token refresh.",
            "- Project bindings stay in `.labbook/`, but long-lived tokens move into the configured shared credential provider.",
        ]
    )


def _load_notion_access_broker_credentials_module():
    module_name = "notion_access_broker.credentials"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"notion_access_broker", module_name}:
            raise
        importlib.invalidate_caches()
        candidate_paths = _candidate_notion_access_broker_src_paths()
        last_exc: ModuleNotFoundError = exc
        searched_paths: list[str] = []
        for candidate in candidate_paths:
            candidate_str = str(candidate)
            searched_paths.append(candidate_str)
            if not candidate.exists():
                continue
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            try:
                return importlib.import_module(module_name)
            except ModuleNotFoundError as retry_exc:
                if retry_exc.name not in {"notion_access_broker", module_name}:
                    raise
                last_exc = retry_exc
                continue

        searched_suffix = ""
        if searched_paths:
            searched_suffix = " Searched: " + ", ".join(searched_paths) + "."
        raise LabbookError(
            "The shared notion-access-broker Python helpers are not installed. Install the notion-access-broker package, "
            f"set {NOTION_ACCESS_BROKER_SRC_ENV_VAR} to the helper repo or src directory, or keep the repo checked out in a searchable sibling path."
            f"{searched_suffix}"
        ) from last_exc


def _normalize_notion_access_broker_src_path(candidate: Path) -> Path:
    normalized = candidate.expanduser().resolve()
    if (normalized / "notion_access_broker").exists():
        return normalized
    src_candidate = normalized / "src"
    if (src_candidate / "notion_access_broker").exists():
        return src_candidate
    return normalized


def _candidate_notion_access_broker_src_paths(*, cwd: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path | None) -> None:
        if path is None:
            return
        normalized = _normalize_notion_access_broker_src_path(path)
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    configured_path = str(os.getenv(NOTION_ACCESS_BROKER_SRC_ENV_VAR) or "").strip()
    if configured_path:
        add_candidate(Path(configured_path))

    add_candidate(Path(__file__).resolve().parents[3] / "notion-access-broker" / "src")

    resolved_cwd = cwd
    if resolved_cwd is None:
        try:
            resolved_cwd = Path.cwd()
        except OSError:
            resolved_cwd = None
    if resolved_cwd is not None:
        resolved_cwd = resolved_cwd.resolve()
        for ancestor in (resolved_cwd, *resolved_cwd.parents):
            add_candidate(ancestor / "notion-access-broker" / "src")

    return candidates


def _store_token_credential(*, integration_id: str, token_payload: dict[str, Any]) -> dict[str, Any]:
    module = _load_notion_access_broker_credentials_module()
    try:
        return module.store_token(integration_id=integration_id, token_payload=token_payload)
    except Exception as exc:  # noqa: BLE001
        raise LabbookError(str(exc)) from exc


def _load_token_credential(
    *,
    integration_id: str,
    credential_provider: str,
    credential_ref: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module = _load_notion_access_broker_credentials_module()
    try:
        return module.load_token(
            integration_id=integration_id,
            credential_provider=credential_provider,
            credential_ref=credential_ref,
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001
        raise LabbookError(str(exc)) from exc


def _list_saved_token_credentials(
    *,
    integration_id: str,
    credential_provider: str | None = None,
) -> list[dict[str, Any]]:
    module = _load_notion_access_broker_credentials_module()
    provider_name = str(credential_provider or "").strip()
    if provider_name:
        try:
            payload = module.list_credentials(integration_id=integration_id, provider_name=provider_name)
        except Exception as exc:  # noqa: BLE001
            raise LabbookError(str(exc)) from exc
        return [item for item in list(payload or []) if isinstance(item, dict)]

    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    last_error: Exception | None = None
    for candidate_provider in ("1password", "keyring"):
        try:
            payload = module.list_credentials(integration_id=integration_id, provider_name=candidate_provider)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        for item in list(payload or []):
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or candidate_provider).strip() or candidate_provider
            credential_ref = str(item.get("credential_ref") or "").strip()
            if not credential_ref:
                continue
            key = (provider, credential_ref)
            if key in seen:
                continue
            seen.add(key)
            collected.append(item)

    if collected:
        return collected
    if last_error is not None:
        raise LabbookError(str(last_error)) from last_error
    return []


def _credential_provider_diagnostics(
    *,
    provider_name: str | None = None,
) -> dict[str, Any] | None:
    module = _load_notion_access_broker_credentials_module()
    diagnostics_fn = getattr(module, "provider_diagnostics", None)
    if not callable(diagnostics_fn):
        return None
    try:
        payload = diagnostics_fn(provider_name=provider_name)
    except Exception as exc:  # noqa: BLE001
        raise LabbookError(str(exc)) from exc
    return payload if isinstance(payload, dict) else None


def _session_is_authenticated(session_payload: dict[str, Any]) -> bool:
    credential_ref = str(session_payload.get("credential_ref") or "").strip()
    access_token = str(session_payload.get("access_token") or "").strip()
    return bool(credential_ref or access_token)


def _session_supports_refresh(session_payload: dict[str, Any]) -> bool:
    credential_ref = str(session_payload.get("credential_ref") or "").strip()
    refresh_token = str(session_payload.get("refresh_token") or "").strip()
    return bool(credential_ref or refresh_token)


def _session_token_payload(session_payload: dict[str, Any]) -> dict[str, Any]:
    credential_provider = str(session_payload.get("credential_provider") or "").strip()
    credential_ref = str(session_payload.get("credential_ref") or "").strip()
    metadata = session_payload.get("credential_metadata") if isinstance(session_payload.get("credential_metadata"), dict) else None
    if credential_provider and credential_ref:
        return _load_token_credential(
            integration_id=INTEGRATION_ID,
            credential_provider=credential_provider,
            credential_ref=credential_ref,
            metadata=metadata,
        )

    access_token = str(session_payload.get("access_token") or "").strip()
    refresh_token = str(session_payload.get("refresh_token") or "").strip()
    if access_token and refresh_token:
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": str(session_payload.get("token_type") or "bearer").strip() or "bearer",
            "bot_id": session_payload.get("bot_id"),
            "workspace_id": session_payload.get("workspace_id"),
            "workspace_name": session_payload.get("workspace_name"),
            "workspace_icon": session_payload.get("workspace_icon"),
            "duplicated_template_id": session_payload.get("duplicated_template_id"),
            "owner": session_payload.get("owner") if isinstance(session_payload.get("owner"), dict) else None,
        }

    raise LabbookError("No Notion access token is configured for this project.")


def _notion_client(session_payload: dict[str, Any]) -> NotionClient:
    token_payload = _session_token_payload(session_payload)
    access_token = str(token_payload.get("access_token") or "").strip()
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
        "selection_scope": _normalize_selection_scope(item.get("selection_scope")),
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
    def normalize_supported_versions(raw_value: Any) -> tuple[int, ...]:
        if not isinstance(raw_value, list) or not raw_value:
            raise LabbookError("Worker backend did not declare supported_api_versions.")
        versions: list[int] = []
        for item in raw_value:
            try:
                version = int(item)
            except (TypeError, ValueError) as exc:
                raise LabbookError(
                    f"Worker backend returned an invalid supported API version: {item!r}."
                ) from exc
            versions.append(version)
        return tuple(versions)

    def decode_payload(raw: str, *, status_code: int | None = None) -> dict[str, Any]:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            if status_code is None:
                raise LabbookError("Worker backend returned invalid JSON.") from exc
            raise LabbookError(f"Backend returned HTTP {status_code} with invalid JSON.") from exc
        if not isinstance(decoded, dict):
            if status_code is None:
                raise LabbookError("Worker backend returned an unexpected payload.")
            raise LabbookError(f"Backend returned HTTP {status_code} with an unexpected payload.")

        supported_api_versions = normalize_supported_versions(decoded.get("supported_api_versions"))
        api_version = decoded.get("api_version")
        if api_version not in supported_api_versions:
            found = repr(api_version)
            if status_code is None:
                raise LabbookError(
                    "Worker backend returned a malformed compatibility envelope. "
                    f"api_version={found}, supported_api_versions={list(supported_api_versions)!r}."
                )
            raise LabbookError(
                f"Backend returned HTTP {status_code} with a malformed compatibility envelope. "
                f"api_version={found}, supported_api_versions={list(supported_api_versions)!r}."
            )
        if api_version not in SUPPORTED_BROKER_API_VERSIONS:
            found = repr(api_version)
            local_supported = list(SUPPORTED_BROKER_API_VERSIONS)
            remote_supported = list(supported_api_versions)
            if status_code is None:
                raise LabbookError(
                    "Worker backend API version mismatch. "
                    f"Client supports {local_supported!r}; backend reported api_version={found} "
                    f"and supported_api_versions={remote_supported!r}."
                )
            raise LabbookError(
                f"Backend returned HTTP {status_code} with incompatible API version. "
                f"Client supports {local_supported!r}; backend reported api_version={found} "
                f"and supported_api_versions={remote_supported!r}."
            )
        return decoded

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": CLIENT_USER_AGENT,
            "X-Notion-Access-Broker-Accept-Api-Versions": BROKER_API_VERSIONS_HEADER,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        decoded = decode_payload(raw, status_code=exc.code)
        message = str(decoded.get("error") or decoded.get("error_code") or exc.reason or "").strip()
        raise LabbookError(f"Backend returned HTTP {exc.code}: {message or exc.reason}") from exc
    except error.URLError as exc:
        raise LabbookError(f"Could not reach the Worker backend: {exc.reason}") from exc

    return decode_payload(raw)


def _terminate_local_handoff_server(project_root: str | Path | None = None) -> bool:
    payload = load_local_handoff_server(project_root) or {}
    pid_raw = payload.get("pid")
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return clear_local_handoff_server(project_root)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    return clear_local_handoff_server(project_root)


def _clear_local_browser_handoff_state(
    project_root: str | Path | None = None,
    *,
    terminate_server: bool = False,
) -> dict[str, bool]:
    terminated_server = _terminate_local_handoff_server(project_root) if terminate_server else False
    cleared_server = clear_local_handoff_server(project_root)
    cleared_handoff = clear_pending_handoff(project_root)
    return {
        "terminated_local_handoff_server": terminated_server,
        "cleared_local_handoff_server": cleared_server or terminated_server,
        "cleared_pending_handoff": cleared_handoff,
    }


def _spawn_persistent_local_handoff_server(
    *,
    project_root: Path,
    session_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    _clear_local_browser_handoff_state(project_root, terminate_server=True)

    command = [
        sys.executable,
        "-m",
        "labbook.local_handoff",
        "--project-root",
        str(project_root),
        "--session-id",
        session_id,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "cwd": str(project_root),
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    deadline = time.monotonic() + LOCAL_HANDOFF_SERVER_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = load_local_handoff_server(project_root) or {}
        if (
            str(payload.get("session_id") or "").strip() == session_id
            and str(payload.get("return_to") or "").strip()
        ):
            return payload
        if process.poll() is not None:
            break
        time.sleep(0.05)

    raise LabbookError("Could not start the local browser handoff listener.")


def _pending_handoff_matches_pending_auth(
    *,
    pending_auth: dict[str, Any] | None,
    pending_handoff: dict[str, Any] | None,
) -> bool:
    if not isinstance(pending_auth, dict) or not isinstance(pending_handoff, dict):
        return False
    expected_session_id = str(pending_auth.get("session_id") or "").strip()
    handoff_session_id = str(pending_handoff.get("session_id") or "").strip()
    handoff_bundle = str(pending_handoff.get("handoff_bundle") or "").strip()
    return bool(expected_session_id and handoff_session_id == expected_session_id and handoff_bundle)


def _finalize_saved_local_browser_handoff(
    *,
    project_root: Path,
    pending_auth: dict[str, Any],
) -> dict[str, Any]:
    pending_handoff = load_pending_handoff(project_root) or {}
    if not pending_handoff:
        raise LabbookError("No saved local browser handoff bundle was found for this project.")

    expected_session_id = str(pending_auth.get("session_id") or "").strip()
    handoff_session_id = str(pending_handoff.get("session_id") or "").strip()
    if not expected_session_id or handoff_session_id != expected_session_id:
        clear_pending_handoff(project_root)
        raise LabbookError("The saved local browser handoff bundle did not match the active pending auth session.")

    handoff_bundle = str(pending_handoff.get("handoff_bundle") or "").strip()
    if not handoff_bundle:
        clear_pending_handoff(project_root)
        raise LabbookError("The saved local browser handoff state did not contain a handoff bundle.")

    result = _complete_auth_handoff(
        project_root=project_root,
        pending_auth=pending_auth,
        handoff_bundle=handoff_bundle,
    )
    clear_pending_handoff(project_root)
    return result


def _save_session_payload(
    project_root: Path,
    *,
    app_url: str,
    oauth_base_url: str,
    token_payload: dict[str, Any],
) -> Path:
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise LabbookError("The OAuth handoff did not contain both access_token and refresh_token.")
    stored = _store_token_credential(integration_id=INTEGRATION_ID, token_payload=token_payload)

    payload = {
        "version": 1,
        "project_root": str(project_root),
        "integration": INTEGRATION_ID,
        "backend_url": app_url,
        "app_url": app_url,
        "oauth_base_url": oauth_base_url,
        "authorized_at": _utc_now(),
        "last_refreshed_at": _utc_now(),
        "credential_provider": str(stored.get("provider") or "").strip(),
        "credential_ref": str(stored.get("credential_ref") or "").strip(),
        "credential_metadata": stored.get("metadata") if isinstance(stored.get("metadata"), dict) else None,
        "token_type": str(token_payload.get("token_type") or "bearer").strip() or "bearer",
        "bot_id": str(token_payload.get("bot_id") or "").strip() or None,
        "workspace_id": str(token_payload.get("workspace_id") or "").strip() or None,
        "workspace_name": str(token_payload.get("workspace_name") or "").strip() or None,
        "workspace_icon": str(token_payload.get("workspace_icon") or "").strip() or None,
        "duplicated_template_id": str(token_payload.get("duplicated_template_id") or "").strip() or None,
        "owner": token_payload.get("owner") if isinstance(token_payload.get("owner"), dict) else None,
        "session_source": "oauth_handoff",
    }
    return save_project_session(project_root, payload)


def _save_attached_credential_session(
    project_root: Path,
    *,
    app_url: str,
    oauth_base_url: str,
    credential_provider: str,
    credential_ref: str,
    credential_metadata: dict[str, Any] | None,
    token_payload: dict[str, Any],
    authorized_at: str | None = None,
    last_refreshed_at: str | None = None,
) -> Path:
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise LabbookError("The saved credential did not contain both access_token and refresh_token.")

    payload = {
        "version": 1,
        "project_root": str(project_root),
        "integration": INTEGRATION_ID,
        "backend_url": app_url,
        "app_url": app_url,
        "oauth_base_url": oauth_base_url,
        "authorized_at": str(authorized_at or "").strip() or _utc_now(),
        "last_refreshed_at": str(last_refreshed_at or "").strip() or _utc_now(),
        "attached_at": _utc_now(),
        "credential_provider": str(credential_provider or "").strip(),
        "credential_ref": str(credential_ref or "").strip(),
        "credential_metadata": credential_metadata if isinstance(credential_metadata, dict) else None,
        "token_type": str(token_payload.get("token_type") or "bearer").strip() or "bearer",
        "bot_id": str(token_payload.get("bot_id") or "").strip() or None,
        "workspace_id": str(token_payload.get("workspace_id") or "").strip() or None,
        "workspace_name": str(token_payload.get("workspace_name") or "").strip() or None,
        "workspace_icon": str(token_payload.get("workspace_icon") or "").strip() or None,
        "duplicated_template_id": str(token_payload.get("duplicated_template_id") or "").strip() or None,
        "owner": token_payload.get("owner") if isinstance(token_payload.get("owner"), dict) else None,
        "session_source": "saved_credential",
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
                selection_scope=str(item.get("selection_scope") or "resource").strip() or "resource",
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
    app_url = str(pending_auth.get("backend_url") or "").strip() or effective_backend_url()
    oauth_base_url = str(pending_auth.get("oauth_base_url") or "").strip() or effective_oauth_base_url()
    decoded = _resolve_handoff_bundle(
        oauth_base_url=oauth_base_url,
        expected_session_id=expected_session_id,
        handoff_bundle=handoff_bundle,
    )
    session_id = str(decoded.get("session_id") or "").strip()
    if not session_id or session_id != expected_session_id:
        raise LabbookError("The OAuth handoff session_id did not match the pending auth request.")
    integration = str(decoded.get("integration") or INTEGRATION_ID).strip() or INTEGRATION_ID
    if integration != INTEGRATION_ID:
        raise LabbookError(f"The OAuth handoff was issued for integration {integration!r}, not {INTEGRATION_ID!r}.")
    token_payload = decoded.get("token")
    if not isinstance(token_payload, dict):
        raise LabbookError("The OAuth handoff did not contain token details.")

    selected_resources_raw = decoded.get("selected_resources")
    selected_resources = [item for item in list(selected_resources_raw or []) if isinstance(item, dict)]

    session_file = _save_session_payload(
        project_root,
        app_url=app_url,
        oauth_base_url=oauth_base_url,
        token_payload=token_payload,
    )
    bindings_payload = _bindings_from_selected_resources(
        selected_resources=selected_resources,
        project_root=project_root,
    )
    binding_file = save_project_bindings(project_root, bindings_payload)
    clear_pending_auth(project_root)
    clear_pending_handoff(project_root)
    _terminate_local_handoff_server(project_root)

    return {
        "project_root": str(project_root),
        "integration": integration,
        "backend_url": app_url,
        "app_url": app_url,
        "oauth_base_url": oauth_base_url,
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
    oauth_base_url: str,
    mode: str,
    session_id: str,
    auth_url: str,
    return_to: str | None,
    timeout_seconds: int | None = None,
    page_limit: int | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(project_root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
        "mode": mode,
        "session_id": session_id,
        "auth_url": auth_url,
        "return_to": return_to,
        "timeout_seconds": timeout_seconds,
        "page_limit": page_limit,
        "started_at": _utc_now(),
    }


def _oauth_start_url(
    *,
    oauth_base_url: str,
    app_url: str,
    project_root: Path,
    session_id: str,
    mode: str,
    return_to: str | None = None,
    page_limit: int = 5000,
) -> str:
    query = {
        "integration": INTEGRATION_ID,
        "mode": mode,
        "session_id": session_id,
        "project_name": project_root.name,
        "page_limit": str(page_limit),
        "continue_to": f"{app_url}/oauth/continue",
    }
    if return_to:
        query["return_to"] = return_to
    return f"{oauth_base_url}/start?{parse.urlencode(query)}"


def _create_selection_continue_url(
    *,
    oauth_base_url: str,
    app_url: str,
    project_root: Path,
    session_id: str,
    mode: str,
    token_payload: dict[str, Any],
    return_to: str | None = None,
    page_limit: int = DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
) -> str:
    payload = _post_backend_json(
        f"{oauth_base_url}/api/create-session",
        {
            "integration": INTEGRATION_ID,
            "mode": mode,
            "session_id": session_id,
            "project_name": project_root.name,
            "page_limit": page_limit,
            "continue_to": f"{app_url}/oauth/continue",
            "return_to": return_to,
            "token": token_payload,
        },
    )
    if not payload.get("ok"):
        raise LabbookError(str(payload.get("error") or "The access broker could not create a reusable selection session."))
    continue_url = str(payload.get("continue_url") or "").strip()
    if not continue_url:
        raise LabbookError("The access broker did not return a selection continue_url.")
    return continue_url


def setup_guide() -> str:
    return _build_setup_guide()


def _annotated_saved_credentials(
    *,
    project_root: Path,
    session_payload: dict[str, Any],
    credential_provider: str | None = None,
) -> list[dict[str, Any]]:
    active_ref = str(session_payload.get("credential_ref") or "").strip()
    active_provider = str(session_payload.get("credential_provider") or "").strip()
    annotated: list[dict[str, Any]] = []
    for item in _list_saved_token_credentials(integration_id=INTEGRATION_ID, credential_provider=credential_provider):
        credential_ref = str(item.get("credential_ref") or "").strip()
        provider = str(item.get("provider") or "").strip()
        display_name = (
            str(item.get("workspace_name") or "").strip()
            or str(item.get("workspace_id") or "").strip()
            or str(item.get("bot_id") or "").strip()
            or credential_ref
        )
        annotated.append(
            {
                **item,
                "display_name": display_name,
                "attached_to_project": bool(
                    active_ref
                    and active_provider
                    and credential_ref == active_ref
                    and provider == active_provider
                ),
            }
        )
    return annotated


def _resolve_project_or_saved_credential(
    *,
    project_root: Path,
    credential_ref: str | None = None,
    credential_provider: str | None = None,
) -> dict[str, Any]:
    session_payload = load_project_session(project_root) or {}
    selected_ref = str(credential_ref or "").strip()
    selected_provider = str(credential_provider or "").strip()

    if not selected_ref and not selected_provider and _session_is_authenticated(session_payload):
        token_payload = _session_token_payload(session_payload)
        return {
            "session_payload": session_payload,
            "credential_provider": str(session_payload.get("credential_provider") or "").strip() or None,
            "credential_ref": str(session_payload.get("credential_ref") or "").strip() or None,
            "credential_metadata": session_payload.get("credential_metadata")
            if isinstance(session_payload.get("credential_metadata"), dict)
            else None,
            "token_payload": token_payload,
            "authorized_at": str(session_payload.get("authorized_at") or "").strip() or None,
            "updated_at": str(session_payload.get("last_refreshed_at") or "").strip() or None,
            "source": "project_session",
        }

    credentials = _annotated_saved_credentials(
        project_root=project_root,
        session_payload=session_payload,
        credential_provider=selected_provider or None,
    )
    if not credentials:
        raise LabbookError("No saved Notion credentials were found in the configured shared credential providers.")

    selected = None
    if selected_ref:
        selected = next(
            (
                item
                for item in credentials
                if str(item.get("credential_ref") or "").strip() == selected_ref
                and (
                    not selected_provider
                    or str(item.get("provider") or "").strip() == selected_provider
                )
            ),
            None,
        )
        if selected is None:
            raise LabbookError(f"Saved credential {selected_ref!r} was not found for {INTEGRATION_ID}.")
    elif len(credentials) == 1:
        selected = credentials[0]
    else:
        raise LabbookError(
            "Multiple saved credentials are available. Call notion_list_saved_credentials first and pass credential_ref explicitly."
        )

    resolved_provider = str(selected.get("provider") or "").strip()
    resolved_ref = str(selected.get("credential_ref") or "").strip()
    resolved_metadata = selected.get("metadata") if isinstance(selected.get("metadata"), dict) else None
    token_payload = _load_token_credential(
        integration_id=INTEGRATION_ID,
        credential_provider=resolved_provider,
        credential_ref=resolved_ref,
        metadata=resolved_metadata,
    )
    return {
        "session_payload": session_payload,
        "credential_provider": resolved_provider,
        "credential_ref": resolved_ref,
        "credential_metadata": resolved_metadata,
        "token_payload": token_payload,
        "authorized_at": str(selected.get("authorized_at") or "").strip() or None,
        "updated_at": str(selected.get("updated_at") or "").strip() or None,
        "source": "saved_credential",
    }


def _public_pending_auth_payload(pending_auth: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(pending_auth, dict) or not pending_auth:
        return None
    return {
        **pending_auth,
        "integration": str(pending_auth.get("integration") or INTEGRATION_ID).strip() or INTEGRATION_ID,
    }


def _connect_decision_payload(
    *,
    authenticated: bool,
    available_saved_credentials: list[dict[str, Any]],
    browser_environment: dict[str, Any],
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    recommended_scope_mode = "bind_existing_scope" if (authenticated or available_saved_credentials) else "expand_oauth_scope"
    recommended_browser_mode = str(browser_environment.get("preferred_browser_flow") or "local_browser").strip() or "local_browser"
    client_prompt_hint = (
        "This is a blocking decision. If the client supports interactive question pickers, map `questions` directly "
        "into those prompts and wait for the user's response before choosing tools. Otherwise show "
        "`manual_prompt_markdown` and wait for a plain-text answer before choosing tools."
    )
    blocking_hint = (
        "Do not choose between bind_existing_scope vs expand_oauth_scope or local_browser vs headless until the user "
        "answers both questions explicitly. Only skip these questions when the user already provided both values."
    )
    manual_prompt_markdown = "\n".join(
        [
            "Please answer two short questions so the agent can choose the right Notion connect flow:",
            "1. Scope",
            f"   - `bind_existing_scope` ({'recommended' if recommended_scope_mode == 'bind_existing_scope' else 'available'}): only choose project bindings from pages or databases the current integration can already access.",
            f"   - `expand_oauth_scope` ({'recommended' if recommended_scope_mode == 'expand_oauth_scope' else 'available'}): reopen the official Notion OAuth root-page chooser to expand what this integration can access.",
            "2. Browser",
            f"   - `local_browser` ({'recommended' if recommended_browser_mode == 'local_browser' else 'available'}): open a browser on the same machine as the MCP server.",
            f"   - `headless` ({'recommended' if recommended_browser_mode == 'headless' else 'available'}): return a URL or handoff-bundle flow for SSH, remote terminals, or another device.",
            "Ask the user to reply with: `scope_mode=<bind_existing_scope|expand_oauth_scope> browser_mode=<local_browser|headless>`.",
        ]
    )

    return {
        "requires_user_choice": True,
        "blocking_hint": blocking_hint,
        "questions": [
            {
                "header": "Scope",
                "id": "scope_mode",
                "question": "Do you need to expand the Notion root pages this integration can access, or only choose project bindings from the current access scope?",
                "recommended_option_id": recommended_scope_mode,
                "options": [
                    {
                        "id": "bind_existing_scope",
                        "label": "Bind Existing Scope",
                        "description": "Only choose which already-authorized pages or databases this project should bind.",
                    },
                    {
                        "id": "expand_oauth_scope",
                        "label": "Reauthorize Scope",
                        "description": "Reopen the official Notion OAuth root-page chooser to expand what this integration can access.",
                    },
                ],
            },
            {
                "header": "Browser",
                "id": "browser_mode",
                "question": "Should this flow open a browser on the MCP host, or stay headless and return a URL or handoff-bundle flow?",
                "recommended_option_id": recommended_browser_mode,
                "options": [
                    {
                        "id": "local_browser",
                        "label": "Open Browser",
                        "description": "Use only when the browser is running on the same machine and can reach the MCP host's localhost callback.",
                    },
                    {
                        "id": "headless",
                        "label": "Headless",
                        "description": "Return a URL or handoff-bundle flow that works better for SSH, remote terminals, and browser-on-another-device setups.",
                    },
                ],
            },
        ],
        "recommended_answers": {
            "scope_mode": recommended_scope_mode,
            "browser_mode": recommended_browser_mode,
        },
        "client_prompt_hint": client_prompt_hint,
        "manual_prompt_markdown": manual_prompt_markdown,
        "manual_response_hint": "Reply with `scope_mode=<bind_existing_scope|expand_oauth_scope> browser_mode=<local_browser|headless>`.",
        "route_templates": [
            {
                "scope_mode": "bind_existing_scope",
                "browser_mode": "local_browser",
                "action_sequence": [
                    {
                        "tool": "notion_selection_browser",
                        "arguments": {"open_browser": True},
                    }
                ],
            },
            {
                "scope_mode": "bind_existing_scope",
                "browser_mode": "headless",
                "action_sequence": [
                    {
                        "tool": "notion_selection_browser",
                        "arguments": {"open_browser": False},
                    }
                ],
            },
            {
                "scope_mode": "expand_oauth_scope",
                "browser_mode": "local_browser",
                "action_sequence": [
                    {
                        "tool": "notion_auth_browser",
                        "arguments": {"open_browser": True},
                    }
                ],
            },
            {
                "scope_mode": "expand_oauth_scope",
                "browser_mode": "headless",
                "action_sequence": [
                    {
                        "tool": "notion_start_headless_auth",
                        "arguments": {},
                    }
                ],
            },
        ],
        "known_authorized_root_pages": [],
        "known_authorized_root_pages_available": False,
        "known_authorized_root_pages_hint": (
            "Agent Labbook cannot currently list the integration's full authorized Notion root pages from local project state alone. "
            "The only local evidence available here is the project's current bindings and whatever appears in the hosted selection UI. "
            "If the page or database you need is missing there, choose Reauthorize Scope."
        ),
        "known_project_bindings": list(resources),
        "next_step_hint": (
            "If you choose Bind Existing Scope but the project is not authenticated yet, attach a saved credential first when one is available. "
            "If the hosted selection UI still does not show the content you need, switch to Reauthorize Scope."
        ),
    }


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    backend_url = effective_backend_url()
    oauth_base_url = effective_oauth_base_url()
    browser_environment = _browser_environment()
    session_payload = load_project_session(root) or {}
    pending_auth = load_pending_auth(root) or {}
    pending_handoff = load_pending_handoff(root) or {}
    local_handoff_server = load_local_handoff_server(root) or {}
    bindings = load_project_bindings(root) or {}
    resources = list(bindings.get("resources") or [])

    pending_auth_stale = pending_auth_is_stale(pending_auth)
    active_pending_auth = {} if pending_auth_stale else pending_auth
    pending_handoff_ready = _pending_handoff_matches_pending_auth(
        pending_auth=active_pending_auth,
        pending_handoff=pending_handoff,
    )

    authenticated = _session_is_authenticated(session_payload)
    bindings_ready = bool(resources)
    available_saved_credentials: list[dict[str, Any]] = []
    saved_credentials_error: str | None = None
    credential_provider_diagnostics: dict[str, Any] | None = None
    credential_provider_diagnostics_error: str | None = None
    try:
        credential_provider_diagnostics = _credential_provider_diagnostics()
    except LabbookError as exc:
        credential_provider_diagnostics_error = str(exc)
    if not active_pending_auth:
        try:
            available_saved_credentials = _annotated_saved_credentials(
                project_root=root,
                session_payload=session_payload,
            )
        except LabbookError as exc:
            saved_credentials_error = str(exc)

    pending_auth_mode = str(active_pending_auth.get("mode") or "").strip()
    if active_pending_auth and not authenticated and pending_handoff_ready:
        recommended_action = "notion_finalize_pending_auth"
    elif active_pending_auth and not authenticated and pending_auth_mode == "local_browser":
        recommended_action = "notion_status"
    elif active_pending_auth and not authenticated:
        recommended_action = "notion_complete_headless_auth"
    elif not authenticated and (saved_credentials_error or credential_provider_diagnostics_error):
        recommended_action = "notion_setup_guide"
    elif not authenticated and len(available_saved_credentials) == 1:
        recommended_action = "notion_attach_saved_credential"
    elif not authenticated and available_saved_credentials:
        recommended_action = "notion_list_saved_credentials"
    elif not authenticated:
        recommended_action = (
            "notion_start_headless_auth"
            if browser_environment["preferred_browser_flow"] == "headless"
            else "notion_auth_browser"
        )
    elif not bindings_ready:
        recommended_action = "notion_bind_resources"
    else:
        recommended_action = "notion_get_api_context"

    pending_handoff_hint = None
    if pending_handoff_ready:
        pending_handoff_hint = "A browser handoff bundle is ready. Call notion_finalize_pending_auth to persist the session and bindings."
    elif active_pending_auth and pending_auth_mode == "local_browser":
        pending_handoff_hint = "The local browser flow is still waiting for the browser handoff. Call notion_status again after the browser says the project is connected."
    elif active_pending_auth and pending_auth_mode == "headless":
        pending_handoff_hint = "Finish the headless auth flow with notion_complete_headless_auth after the browser shows a handoff bundle."
    elif pending_auth_stale:
        pending_handoff_hint = "The saved pending auth state has expired. Start a new auth flow or attach a saved credential."

    browser_auth_hint = (
        "Use notion_auth_browser only when the browser and MCP server are on the same machine. It starts a "
        "localhost handoff listener, so after the browser says the project is connected, call notion_status. "
        f"If pending_handoff_ready is true, finish with notion_finalize_pending_auth. Recommended browser auth timeout_seconds: {DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS}."
    )
    if not browser_environment["recommended_open_browser"]:
        browser_auth_hint += (
            f" Current environment note: {browser_environment['reason']} Prefer notion_start_headless_auth or pass open_browser=false."
        )
    scope_choice_hint = (
        "Use notion_selection_browser only to choose project bindings from Notion content that the current integration is already authorized to access. "
        "If the page or database you need is missing because the integration was not granted that root page yet, re-run OAuth with notion_auth_browser "
        "or notion_start_headless_auth instead of notion_selection_browser."
    )
    connect_decision = _connect_decision_payload(
        authenticated=authenticated,
        available_saved_credentials=available_saved_credentials,
        browser_environment=browser_environment,
        resources=resources,
    )

    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
        "redirect_uri": oauth_callback_uri(oauth_base_url),
        "auth_modes": ["local_browser", "headless"],
        "preferred_browser_flow": browser_environment["preferred_browser_flow"],
        "recommended_open_browser": browser_environment["recommended_open_browser"],
        "browser_environment_hint": browser_environment["reason"],
        "browser_environment": browser_environment,
        "scope_choice_hint": scope_choice_hint,
        "connect_decision": connect_decision,
        "recommended_browser_auth_timeout_seconds": DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
        "recommended_browser_auth_page_limit": DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
        "browser_auth_hint": browser_auth_hint,
        "headless_auth_hint": (
            "If the browser handoff cannot get back to the MCP server, finish the flow with notion_complete_headless_auth "
            "using the handoff bundle shown on the page."
        ),
        "browser_auth_page_limit_hint": (
            f"page_limit controls the size of the initial recent-items catalog. Values below {MIN_BROWSER_AUTH_PAGE_LIMIT} "
            "are clamped, and remote search still covers the full shared workspace."
        ),
        "authenticated": authenticated,
        "refresh_supported": _session_supports_refresh(session_payload),
        "credential_provider": str(session_payload.get("credential_provider") or "").strip() or None,
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "available_saved_credentials_count": len(available_saved_credentials),
        "available_saved_credentials": available_saved_credentials,
        "saved_credentials_error": saved_credentials_error,
        "credential_provider_diagnostics": credential_provider_diagnostics,
        "credential_provider_diagnostics_error": credential_provider_diagnostics_error,
        "bound_resource_count": len(resources),
        "default_resource_alias": bindings.get("default_resource_alias"),
        "resources": resources,
        "session_path": str(session_path(root)),
        "binding_path": str(bindings_path(root)),
        "pending_auth_path": str(pending_auth_path(root)),
        "pending_handoff_path": str(pending_handoff_path(root)),
        "local_handoff_server_path": str(local_handoff_server_path(root)),
        "pending_auth": _public_pending_auth_payload(pending_auth),
        "pending_auth_stale": pending_auth_stale,
        "pending_handoff_ready": pending_handoff_ready,
        "pending_handoff": {
            "session_id": pending_handoff.get("session_id"),
            "received_at": pending_handoff.get("received_at"),
            "return_to": pending_handoff.get("return_to"),
        }
        if pending_handoff
        else None,
        "pending_handoff_hint": pending_handoff_hint,
        "local_handoff_server": local_handoff_server or None,
        "ready": authenticated and bindings_ready,
        "recommended_action": recommended_action,
        "setup_guide_resource_uri": SETUP_GUIDE_RESOURCE_URI,
        "status_resource_uri": STATUS_RESOURCE_URI,
        "bindings_resource_uri": BINDINGS_RESOURCE_URI,
    }


def project_status_resource(project_root: str | Path | None = None) -> str:
    return json.dumps(status(project_root), ensure_ascii=False, indent=2, sort_keys=True)


def project_bindings_resource(project_root: str | Path | None = None) -> str:
    return json.dumps(list_bindings(project_root), ensure_ascii=False, indent=2, sort_keys=True)


def auth_browser(
    *,
    project_root: str | Path | None = None,
    timeout_seconds: int | str | None = DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    open_browser: bool = True,
    page_limit: int | str | None = DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    _clear_stale_pending_auth_if_needed(root)
    backend_url = effective_backend_url()
    oauth_base_url = effective_oauth_base_url()
    browser_environment = _browser_environment()
    session_id = uuid4().hex
    wait_timeout_seconds = normalize_browser_auth_timeout_seconds(timeout_seconds)
    normalized_page_limit = normalize_browser_auth_page_limit(page_limit)

    if open_browser and not browser_environment["recommended_open_browser"]:
        result = start_headless_auth(
            project_root=root,
            page_limit=normalized_page_limit,
        )
        result["auth_mode"] = "headless"
        result["auto_switched_to_headless"] = True
        result["reason"] = (
            f"{browser_environment['reason']} This request was switched to headless auth instead of starting a localhost browser handoff."
        )
        return result

    if not open_browser:
        result = start_headless_auth(
            project_root=root,
            page_limit=normalized_page_limit,
        )
        result["auth_mode"] = "headless"
        result["auto_switched_to_headless"] = True
        result["reason"] = (
            "notion_auth_browser was called with open_browser=false, so this request was switched to headless auth."
        )
        return result

    server_payload = _spawn_persistent_local_handoff_server(
        project_root=root,
        session_id=session_id,
        timeout_seconds=wait_timeout_seconds,
    )
    return_to = str(server_payload.get("return_to") or "").strip()
    if not return_to:
        raise LabbookError("The local browser handoff listener did not report a return_to URL.")

    auth_url = _oauth_start_url(
        oauth_base_url=oauth_base_url,
        app_url=backend_url,
        project_root=root,
        session_id=session_id,
        mode="local_browser",
        return_to=return_to,
        page_limit=normalized_page_limit,
    )
    save_pending_auth(
        root,
        _pending_auth_payload(
            project_root=root,
            backend_url=backend_url,
            oauth_base_url=oauth_base_url,
            mode="local_browser",
            session_id=session_id,
            auth_url=auth_url,
            return_to=return_to,
            timeout_seconds=wait_timeout_seconds,
            page_limit=normalized_page_limit,
        ),
    )

    opened = _open_browser_url(auth_url) if open_browser else False
    if open_browser and not opened:
        clear_pending_auth(root)
        _clear_local_browser_handoff_state(root, terminate_server=True)
        result = start_headless_auth(
            project_root=root,
            page_limit=normalized_page_limit,
        )
        result["auth_mode"] = "headless"
        result["auto_switched_to_headless"] = True
        result["reason"] = (
            "A local browser could not be opened from the current environment, so this request was switched to "
            "headless auth."
        )
        return result

    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
        "auth_mode": "local_browser",
        "auth_url": auth_url,
        "session_id": session_id,
        "return_to": return_to,
        "timeout_seconds": wait_timeout_seconds,
        "page_limit": normalized_page_limit,
        "browser_opened": bool(opened),
        "local_handoff_server": server_payload,
        "recommended_next_action": "notion_status",
        "agent_response_hint": _url_relay_hint("auth_url"),
        "instructions": (
            "Finish the Notion consent and selection flow in the browser. The localhost handoff listener will keep "
            "running in the background even if this MCP tool call returns early. After the browser says the project "
            "is connected, call notion_status. If pending_handoff_ready is true, call notion_finalize_pending_auth. "
            "If the browser shows a handoff bundle instead, finish with notion_complete_headless_auth."
        ),
    }


def selection_browser(
    *,
    project_root: str | Path | None = None,
    timeout_seconds: int | str | None = DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    open_browser: bool = True,
    page_limit: int | str | None = DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    credential_ref: str | None = None,
    credential_provider: str | None = None,
    replace_existing_bindings: bool = False,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    _clear_stale_pending_auth_if_needed(root)
    backend_url = effective_backend_url()
    oauth_base_url = effective_oauth_base_url()
    browser_environment = _browser_environment()
    session_id = uuid4().hex
    wait_timeout_seconds = normalize_browser_auth_timeout_seconds(timeout_seconds)
    normalized_page_limit = normalize_browser_auth_page_limit(page_limit)
    existing_bindings = load_project_bindings(root) or {"resources": []}
    existing_resources = list(existing_bindings.get("resources") or [])
    if existing_resources and not replace_existing_bindings:
        raise LabbookError(
            "This project already has bound Notion resources. Pass replace_existing_bindings=true before reopening the browser selection UI."
        )

    resolved = _resolve_project_or_saved_credential(
        project_root=root,
        credential_ref=credential_ref,
        credential_provider=credential_provider,
    )
    token_payload = dict(resolved.get("token_payload") or {})

    if open_browser and not browser_environment["recommended_open_browser"]:
        result = selection_browser(
            project_root=root,
            timeout_seconds=wait_timeout_seconds,
            open_browser=False,
            page_limit=normalized_page_limit,
            credential_ref=str(resolved.get("credential_ref") or "").strip() or None,
            credential_provider=str(resolved.get("credential_provider") or "").strip() or None,
            replace_existing_bindings=replace_existing_bindings,
        )
        result["auto_switched_to_headless"] = True
        result["reason"] = (
            f"{browser_environment['reason']} This request was switched to a headless selection URL instead of starting a localhost browser handoff."
        )
        return result

    if not open_browser:
        continue_url = _create_selection_continue_url(
            oauth_base_url=oauth_base_url,
            app_url=backend_url,
            project_root=root,
            session_id=session_id,
            mode="headless",
            token_payload=token_payload,
            page_limit=normalized_page_limit,
        )
        save_pending_auth(
            root,
            _pending_auth_payload(
                project_root=root,
                backend_url=backend_url,
                oauth_base_url=oauth_base_url,
                mode="headless",
                session_id=session_id,
                auth_url=continue_url,
                return_to=None,
                page_limit=normalized_page_limit,
            ),
        )
        return {
            "project_root": str(root),
            "integration": INTEGRATION_ID,
            "backend_url": backend_url,
            "oauth_base_url": oauth_base_url,
            "selection_mode": "headless",
            "selection_url": continue_url,
            "session_id": session_id,
            "page_limit": normalized_page_limit,
            "credential_provider": resolved.get("credential_provider"),
            "credential_ref": resolved.get("credential_ref"),
            "replaces_existing_bindings": bool(existing_resources),
            "agent_response_hint": _url_relay_hint("selection_url"),
            "instructions": (
                "Open selection_url in any browser. When relaying it to the user, print that raw URL exactly once on "
                "its own line. Then choose the Notion pages or data sources for this project, "
                "then finish with notion_complete_headless_auth if the browser shows a handoff bundle."
            ),
        }

    server_payload = _spawn_persistent_local_handoff_server(
        project_root=root,
        session_id=session_id,
        timeout_seconds=wait_timeout_seconds,
    )
    return_to = str(server_payload.get("return_to") or "").strip()
    if not return_to:
        raise LabbookError("The local browser handoff listener did not report a return_to URL.")

    continue_url = _create_selection_continue_url(
        oauth_base_url=oauth_base_url,
        app_url=backend_url,
        project_root=root,
        session_id=session_id,
        mode="local_browser",
        token_payload=token_payload,
        return_to=return_to,
        page_limit=normalized_page_limit,
    )
    save_pending_auth(
        root,
        _pending_auth_payload(
            project_root=root,
            backend_url=backend_url,
            oauth_base_url=oauth_base_url,
            mode="local_browser",
            session_id=session_id,
            auth_url=continue_url,
            return_to=return_to,
            timeout_seconds=wait_timeout_seconds,
            page_limit=normalized_page_limit,
        ),
    )

    opened = _open_browser_url(continue_url)
    if not opened:
        clear_pending_auth(root)
        _clear_local_browser_handoff_state(root, terminate_server=True)
        return selection_browser(
            project_root=root,
            timeout_seconds=wait_timeout_seconds,
            open_browser=False,
            page_limit=normalized_page_limit,
            credential_ref=str(resolved.get("credential_ref") or "").strip() or None,
            credential_provider=str(resolved.get("credential_provider") or "").strip() or None,
            replace_existing_bindings=replace_existing_bindings,
        )

    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
        "selection_mode": "local_browser",
        "selection_url": continue_url,
        "session_id": session_id,
        "return_to": return_to,
        "timeout_seconds": wait_timeout_seconds,
        "page_limit": normalized_page_limit,
        "credential_provider": resolved.get("credential_provider"),
        "credential_ref": resolved.get("credential_ref"),
        "browser_opened": True,
        "local_handoff_server": server_payload,
        "replaces_existing_bindings": bool(existing_resources),
        "recommended_next_action": "notion_status",
        "agent_response_hint": _url_relay_hint("selection_url"),
        "instructions": (
            "Finish the Notion resource selection flow in the browser. After the browser says the project is connected, "
            "call notion_status. If pending_handoff_ready is true, call notion_finalize_pending_auth. If the browser "
            "shows a handoff bundle instead, finish with notion_complete_headless_auth."
        ),
    }


def start_headless_auth(
    *,
    project_root: str | Path | None = None,
    page_limit: int | str | None = DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    _clear_stale_pending_auth_if_needed(root)
    backend_url = effective_backend_url()
    oauth_base_url = effective_oauth_base_url()
    session_id = uuid4().hex
    normalized_page_limit = normalize_browser_auth_page_limit(page_limit)
    _clear_local_browser_handoff_state(root, terminate_server=True)
    auth_url = _oauth_start_url(
        oauth_base_url=oauth_base_url,
        app_url=backend_url,
        project_root=root,
        session_id=session_id,
        mode="headless",
        page_limit=normalized_page_limit,
    )
    save_pending_auth(
        root,
        _pending_auth_payload(
            project_root=root,
            backend_url=backend_url,
            oauth_base_url=oauth_base_url,
            mode="headless",
            session_id=session_id,
            auth_url=auth_url,
            return_to=None,
            page_limit=normalized_page_limit,
        ),
    )
    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
        "auth_url": auth_url,
        "session_id": session_id,
        "page_limit": normalized_page_limit,
        "agent_response_hint": _url_relay_hint("auth_url"),
        "instructions": (
            "Open auth_url in any browser. When relaying it to the user, print that raw URL exactly once on its own "
            "line. Then finish the Notion consent screen, choose project bindings on the "
            "Labbook page, then paste the resulting handoff bundle into notion_complete_headless_auth."
        ),
    }


def finalize_pending_auth(
    *,
    project_root: str | Path | None = None,
    handoff_bundle: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    pending_auth = load_pending_auth(root) or {}
    if not pending_auth:
        raise LabbookError("No pending auth session was found for this project.")
    if pending_auth_is_stale(pending_auth):
        _clear_stale_pending_auth_if_needed(root)
        raise LabbookError("The pending auth session expired and was cleared. Start a new auth flow.")

    normalized_handoff_bundle = str(handoff_bundle or "").strip()
    if normalized_handoff_bundle:
        return _complete_auth_handoff(
            project_root=root,
            pending_auth=pending_auth,
            handoff_bundle=normalized_handoff_bundle,
        )

    return _finalize_saved_local_browser_handoff(
        project_root=root,
        pending_auth=pending_auth,
    )


def complete_headless_auth(
    *,
    project_root: str | Path | None = None,
    handoff_bundle: str,
) -> dict[str, Any]:
    return finalize_pending_auth(
        project_root=project_root,
        handoff_bundle=handoff_bundle,
    )


def list_saved_credentials(
    *,
    project_root: str | Path | None = None,
    credential_provider: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root) or {}
    credentials = _annotated_saved_credentials(
        project_root=root,
        session_payload=session_payload,
        credential_provider=credential_provider,
    )
    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "credential_provider": str(credential_provider or "").strip() or None,
        "saved_credential_count": len(credentials),
        "credentials": credentials,
    }


def attach_saved_credential(
    *,
    project_root: str | Path | None = None,
    credential_ref: str | None = None,
    credential_provider: str | None = None,
    clear_bindings: bool = False,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    _clear_stale_pending_auth_if_needed(root)
    pending_auth = load_pending_auth(root) or {}
    if pending_auth:
        raise LabbookError("This project already has a pending auth flow. Finish it or clear it before attaching a saved credential.")

    existing_bindings = load_project_bindings(root) or {"resources": []}
    existing_resources = list(existing_bindings.get("resources") or [])
    if existing_resources and not clear_bindings:
        raise LabbookError(
            "This project already has bound Notion resources. Pass clear_bindings=true before attaching a different saved credential."
        )

    credentials = _annotated_saved_credentials(
        project_root=root,
        session_payload=load_project_session(root) or {},
        credential_provider=credential_provider,
    )
    if not credentials:
        raise LabbookError("No saved Notion credentials were found in the configured shared credential provider.")

    selected_ref = str(credential_ref or "").strip()
    selected = None
    if selected_ref:
        selected = next(
            (
                item
                for item in credentials
                if str(item.get("credential_ref") or "").strip() == selected_ref
            ),
            None,
        )
        if selected is None:
            raise LabbookError(f"Saved credential {selected_ref!r} was not found for {INTEGRATION_ID}.")
    elif len(credentials) == 1:
        selected = credentials[0]
    else:
        raise LabbookError("Multiple saved credentials are available. Pass credential_ref explicitly.")

    resolved_provider = str(selected.get("provider") or "").strip()
    resolved_ref = str(selected.get("credential_ref") or "").strip()
    resolved_metadata = selected.get("metadata") if isinstance(selected.get("metadata"), dict) else None
    token_payload = _load_token_credential(
        integration_id=INTEGRATION_ID,
        credential_provider=resolved_provider,
        credential_ref=resolved_ref,
        metadata=resolved_metadata,
    )

    if clear_bindings and existing_resources:
        clear_project_bindings(root)
    _clear_local_browser_handoff_state(root, terminate_server=True)
    save_path = _save_attached_credential_session(
        root,
        app_url=effective_backend_url(),
        oauth_base_url=effective_oauth_base_url(),
        credential_provider=resolved_provider,
        credential_ref=resolved_ref,
        credential_metadata=resolved_metadata,
        token_payload=token_payload,
        authorized_at=str(selected.get("authorized_at") or "").strip() or None,
        last_refreshed_at=str(selected.get("updated_at") or "").strip() or None,
    )

    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": effective_backend_url(),
        "oauth_base_url": effective_oauth_base_url(),
        "credential_provider": resolved_provider,
        "credential_ref": resolved_ref,
        "workspace_name": str(token_payload.get("workspace_name") or "").strip() or None,
        "workspace_id": str(token_payload.get("workspace_id") or "").strip() or None,
        "bot_id": str(token_payload.get("bot_id") or "").strip() or None,
        "session_path": str(save_path),
        "binding_path": str(bindings_path(root)),
        "cleared_bindings": bool(clear_bindings and existing_resources),
        "attached_existing_credential": True,
    }


def refresh_session(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root) or {}
    token_payload = _session_token_payload(session_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not refresh_token:
        raise LabbookError("No refresh token is available for this project.")

    backend_url = str(session_payload.get("backend_url") or "").strip() or effective_backend_url()
    oauth_base_url = str(session_payload.get("oauth_base_url") or "").strip() or effective_oauth_base_url()
    payload = _post_backend_json(
        f"{oauth_base_url}/api/refresh",
        {
            "integration": INTEGRATION_ID,
            "refresh_token": refresh_token,
        },
    )
    if not payload.get("ok"):
        raise LabbookError(str(payload.get("error") or "Worker refresh failed."))
    token_payload = payload.get("token")
    if not isinstance(token_payload, dict):
        raise LabbookError("Worker refresh response did not contain a token payload.")

    _save_session_payload(
        root,
        app_url=backend_url,
        oauth_base_url=oauth_base_url,
        token_payload=token_payload,
    )
    return {
        "project_root": str(root),
        "integration": INTEGRATION_ID,
        "backend_url": backend_url,
        "oauth_base_url": oauth_base_url,
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
    existing_session = load_project_session(root) or {}
    cleared_session = clear_project_session(root)
    cleared_pending = clear_pending_auth(root)
    handoff_cleanup = _clear_local_browser_handoff_state(root, terminate_server=True)
    cleared_bindings = clear_project_bindings(root) if clear_bindings else False
    return {
        "project_root": str(root),
        "cleared_session": cleared_session,
        "cleared_pending_auth": cleared_pending,
        "cleared_pending_handoff": handoff_cleanup["cleared_pending_handoff"],
        "cleared_local_handoff_server": handoff_cleanup["cleared_local_handoff_server"],
        "cleared_bindings": cleared_bindings,
        "shared_credentials_retained": bool(str(existing_session.get("credential_ref") or "").strip()),
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
                selection_scope=_normalize_selection_scope(item.get("selection_scope")),
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
    token_payload = _session_token_payload(session_payload)
    access_token = str(token_payload.get("access_token") or "").strip()
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
            "-H 'Authorization: Bearer ${NOTION_TOKEN}' "
            f"-H 'Notion-Version: {DEFAULT_NOTION_VERSION}' "
            "-H 'Content-Type: application/json' "
            f"'{endpoint}'"
        )

    return {
        "project_root": str(root),
        "api_base": NOTION_API_BASE,
        "notion_version": DEFAULT_NOTION_VERSION,
        "docs_reference": "https://developers.notion.com/reference/intro",
        "docs_versioning": "https://developers.notion.com/reference/versioning",
        "access_token": access_token,
        "credential_provider": str(session_payload.get("credential_provider") or "").strip() or None,
        "headers": headers,
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "default_resource_alias": default_alias,
        "default_binding": default_binding,
        "resources": resources,
        "binding_model": "explicit_roots_with_selection_scope",
        "selection_scope_note": (
            "A binding with selection_scope='subtree' represents an explicitly selected root resource and should be "
            "treated as including nested content under that root."
        ),
        "binding_path": str(bindings_path(root)),
        "session_path": str(session_path(root)),
        "refresh_supported": bool(str(token_payload.get("refresh_token") or "").strip()),
        "refresh_tool": "notion_refresh_session",
        "usage": "Use the official Notion REST API directly with this public integration access token and these bound resources. Treat the bearer token like a password: keep it out of command history, logs, and chat transcripts. If the endpoint shape is uncertain, check the latest Notion API reference first.",
        "curl_example": curl_example,
    }
