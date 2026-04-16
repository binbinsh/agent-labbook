"""Authentication and project-session management."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .browser_ui import BrowserLaunchPolicy, likely_headless_environment
from .notion import NOTION_API_BASE, NotionClient, build_list_bindings_payload
from .state import (
    DEFAULT_NOTION_VERSION,
    KEYRING_SERVICE_NAME,
    TOKEN_ENV_VAR,
    LabbookError,
    bindings_path,
    clear_project_bindings,
    clear_project_session,
    load_project_session,
    resolve_project_root,
    save_project_session,
    session_path,
)
from .storage import (
    keychain_backend_name,
    keychain_backend_status,
    keychain_delete_token,
    keychain_retrieve_token,
    keychain_store_token,
)

logger = logging.getLogger("labbook.auth")

SETUP_GUIDE_RESOURCE_URI = "labbook://agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/status{?project_root}"
BINDINGS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/bindings{?project_root}"
DEFAULT_NOTION_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"
NOTION_INTEGRATION_GUIDE_URL = (
    "https://developers.notion.com/guides/get-started/create-a-notion-integration"
)
SUPPORTED_STORAGE_BACKENDS = ("keychain",)
DEFAULT_PERSISTENT_STORAGE = "keychain"


def setup_guide() -> str:
    return "\n".join(
        [
            "# Notion Agent Labbook Internal Integration Setup",
            "",
            "Notion Agent Labbook uses a Notion Internal Integration secret directly. There is no OAuth broker, browser handoff, or hosted Worker in this version.",
            "",
            "## Recommended Flow",
            "",
            "1. Create a Notion Internal Integration and share the target pages or data sources with it.",
            "2. Run `uvx agent-labbook configure-secret --storage keychain` to save the secret locally without echo.",
            "3. Bind the resources you want to use with exact URLs, the local browser chooser, or MCP search and discovery.",
            "4. Call `notion_get_api_context` only when you are ready to use the official Notion API.",
            "",
            "Default workstation path: keychain.",
            f"Use `{TOKEN_ENV_VAR}` only for CI or temporary overrides.",
            "",
            "## Security Notes",
            "",
            "- `NOTION_AGENT_LABBOOK_TOKEN` takes precedence over any locally stored secret for the current process and is intended for CI or temporary overrides.",
            "- `agent-labbook configure-secret` uses a local hidden prompt so the secret does not need to be pasted into chat or shell history.",
            "- The secret is stored in the local system credential store through Python `keyring`.",
            "- Treat the integration secret like a password. Do not paste it into chat transcripts, logs, or committed files.",
            "",
            "## Notion Resources",
            "",
            f"- Integrations dashboard: {DEFAULT_NOTION_INTEGRATIONS_URL}",
            f"- Setup guide: {NOTION_INTEGRATION_GUIDE_URL}",
            "",
            "## Markdown Content APIs",
            "",
            "If your content already exists as markdown, prefer Notion's markdown content endpoints over manual block assembly.",
            "",
            "- `POST /v1/pages` with `markdown`",
            "- `GET /v1/pages/{page_id}/markdown`",
            "- `PATCH /v1/pages/{page_id}/markdown`",
            "",
            "Reference: https://developers.notion.com/guides/data-apis/working-with-markdown-content",
        ]
    )


def _session_payload(
    *,
    project_root: Path,
    storage: str,
    workspace_name: str | None,
    workspace_id: str | None,
    bot_id: str | None,
    bot_owner_type: str | None,
    **kw: Any,
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "storage": storage,
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "bot_owner_type": bot_owner_type,
        "keyring_service": kw.get("keyring_service"),
        "keyring_account": kw.get("keyring_account"),
        "configured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notion_version": DEFAULT_NOTION_VERSION,
    }


# ---------------------------------------------------------------------------
# Storage resolution
# ---------------------------------------------------------------------------


def _available_storage_backends() -> list[dict[str, Any]]:
    """Probe keychain availability without blocking the MCP loop.

    The probe runs in a daemon thread and is fenced by the configured
    probe-timeout budget (``LABBOOK_PROBE_TIMEOUT_SECONDS``) plus a small
    headroom, so a locked Secret Service / keychain daemon can never stall
    a ``tools/call`` long enough for the client to close the stdio transport.
    """
    from .storage import _probe_timeout_default  # local import to avoid cycles

    timeout = _probe_timeout_default()
    join_budget = max(0.5, timeout + 1.0)

    result: dict[str, dict[str, Any]] = {}

    def _run_keychain() -> None:
        try:
            result["keychain"] = keychain_backend_status(probe_timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - defensive: never raise past join
            result["keychain"] = {
                "backend": "keychain",
                "display_name": "System Keychain",
                "available": False,
                "selected_by_default": False,
                "reason": f"Keychain probe crashed: {exc}",
                "details": {"keyring_backend": "unknown"},
            }

    thread = threading.Thread(
        target=_run_keychain, name="labbook-probe-keychain", daemon=True
    )
    thread.start()
    thread.join(timeout=join_budget)

    keychain_entry = result.get("keychain") or {
        "backend": "keychain",
        "display_name": "System Keychain",
        "available": False,
        "selected_by_default": False,
        "reason": f"System Keychain probe did not finish within {join_budget:.1f}s.",
        "details": {"probe_timed_out": True},
    }
    backends = [keychain_entry]
    available = [b["backend"] for b in backends if b.get("available")]
    recommended = (
        DEFAULT_PERSISTENT_STORAGE if DEFAULT_PERSISTENT_STORAGE in available else None
    )
    for b in backends:
        b["selected_by_default"] = b.get("backend") == recommended
    return backends


def _resolve_storage_backend(
    requested: str | None = None,
    *,
    available_backends: list[dict[str, Any]] | None = None,
    current_backend: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    backends = available_backends or _available_storage_backends()
    available = {b["backend"]: b for b in backends if b.get("available")}
    clean = str(requested or "auto").strip().lower() or "auto"
    current = str(current_backend or "").strip().lower() or None
    if clean not in {"auto", *SUPPORTED_STORAGE_BACKENDS}:
        raise LabbookError("storage must be one of: auto, keychain.")
    if clean == "auto":
        if current in available:
            return current, backends
        if DEFAULT_PERSISTENT_STORAGE in available:
            return DEFAULT_PERSISTENT_STORAGE, backends
        raise LabbookError(
            f"No supported secret storage backends are available. Set {TOKEN_ENV_VAR} instead."
        )
    if clean not in available:
        reason = next(
            (b.get("reason") for b in backends if b.get("backend") == clean), None
        )
        raise LabbookError(
            f"storage={clean!r} is not available.{f' {reason}' if reason else ''}"
        )
    return clean, backends


def _configured_storage(session_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(session_payload, dict):
        return None
    v = str(session_payload.get("storage") or "").strip().lower()
    return v if v in set(SUPPORTED_STORAGE_BACKENDS) else None


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _token_context(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session = load_project_session(root)
    env_token = str(os.environ.get(TOKEN_ENV_VAR) or "").strip()
    backend = _configured_storage(session)
    if env_token:
        return {
            "project_root": root,
            "session": session,
            "token": env_token,
            "token_source": "env",
            "storage": backend,
            "env_token_present": True,
            "keyring_error": None,
            "storage_error": None,
        }
    keyring_error: str | None = None
    token: str | None = None
    token_source: str | None = None
    if backend == "keychain":
        token, keyring_error = keychain_retrieve_token(session)
        token_source = "keychain" if token else None
    return {
        "project_root": root,
        "session": session,
        "token": token,
        "token_source": token_source,
        "storage": backend,
        "env_token_present": False,
        "keyring_error": keyring_error,
        "storage_error": keyring_error,
    }


def _require_token_context(project_root: str | Path | None = None) -> dict[str, Any]:
    ctx = _token_context(project_root)
    if ctx["token"]:
        return ctx
    raise LabbookError(
        "This project does not have a usable Notion Internal Integration secret. "
        "Run notion_prepare_internal_integration and then prefer agent-labbook configure-secret "
        "(which defaults to keychain when available), "
        "or use notion_configure_internal_integration when you can safely provide the secret directly, "
        f"or set {TOKEN_ENV_VAR}."
    )


def notion_client_for_project(
    project_root: str | Path | None = None,
) -> tuple[NotionClient, dict[str, Any]]:
    ctx = _require_token_context(project_root)
    return NotionClient(token=str(ctx["token"])), ctx


def _secret_plan(
    *,
    token_context: dict[str, Any],
    storage_default: str | None,
) -> dict[str, str]:
    configured_storage = token_context.get("storage")
    token_source = token_context.get("token_source")
    env_token_present = bool(token_context.get("env_token_present"))

    if configured_storage == "keychain" and token_source == "keychain":
        return {
            "mode": "keychain",
            "reason": (
                "This project is already using the local system keychain successfully, "
                "so keeping that backend is the least disruptive option."
            ),
        }

    if storage_default == "keychain":
        reason = (
            "System keychain is the default recommendation for persistent local "
            "development on this machine."
        )
        if env_token_present:
            reason += (
                f" The current process is using {TOKEN_ENV_VAR}, but keychain is the "
                "better long-lived default."
            )
        return {"mode": "keychain", "reason": reason}

    return {
        "mode": "env",
        "reason": (
            f"No supported local secret backend is available, so {TOKEN_ENV_VAR} is "
            "the recommended option."
        ),
    }


# ---------------------------------------------------------------------------
# prepare / configure
# ---------------------------------------------------------------------------


def prepare_internal_integration(
    *, project_root: str | Path | None = None, open_browser: bool | None = None
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    storage_options = _available_storage_backends()
    available = [b["backend"] for b in storage_options if b.get("available")]
    storage_default = (
        DEFAULT_PERSISTENT_STORAGE if DEFAULT_PERSISTENT_STORAGE in available else None
    )
    cmd = (
        f"uvx agent-labbook configure-secret --storage {storage_default}"
        if storage_default
        else None
    )
    launch_policy = BrowserLaunchPolicy.from_preference(open_browser)
    launch_result = launch_policy.launch(DEFAULT_NOTION_INTEGRATIONS_URL)
    return {
        "project_root": str(root),
        "notion_integrations_url": DEFAULT_NOTION_INTEGRATIONS_URL,
        "notion_docs_url": NOTION_INTEGRATION_GUIDE_URL,
        "browser_opened": launch_result.opened,
        "open_browser_attempted": launch_result.attempted,
        "storage_options": storage_options,
        "storage_default": storage_default,
        "storage_choice_required": False,
        "secret_label": "Internal Integration Secret",
        "setup_steps": [
            "Create a Notion Internal Integration and share the target pages or data sources with it.",
            f"Run `{cmd}` for a local hidden prompt."
            if cmd
            else "If no local secret backend is available, use NOTION_AGENT_LABBOOK_TOKEN as a process-scoped override.",
            "Bind the resources you want to use.",
            "Call notion_get_api_context only when you are ready to use the official Notion API.",
        ],
        "recommended_next_action": "notion_configure_internal_integration",
        "recommended_local_command": cmd,
    }


def configure_internal_integration(
    *,
    secret: str,
    project_root: str | Path | None = None,
    storage: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    clean_secret = str(secret or "").strip()
    if not clean_secret:
        raise LabbookError("secret is required.")
    session = load_project_session(root)
    resolved_backend, storage_options = _resolve_storage_backend(
        storage, current_backend=_configured_storage(session)
    )
    client = NotionClient(token=clean_secret)
    me = client.get_me()
    bot = me.get("bot") if isinstance(me.get("bot"), dict) else {}
    owner = bot.get("owner") if isinstance(bot.get("owner"), dict) else {}
    workspace_name = str(bot.get("workspace_name") or "").strip() or None
    workspace_id = str(bot.get("workspace_id") or "").strip() or None
    bot_id = str(me.get("id") or "").strip() or None
    bot_owner_type = str(owner.get("type") or "").strip() or None
    kw: dict[str, Any] = {
        "project_root": root,
        "storage": resolved_backend,
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "bot_owner_type": bot_owner_type,
    }
    if resolved_backend == "keychain":
        ks, ka = keychain_store_token(project_root=root, token=clean_secret)
        kw["keyring_service"] = ks
        kw["keyring_account"] = ka
    else:
        raise LabbookError(f"Unsupported storage backend: {resolved_backend}")
    save_project_session(root, _session_payload(**kw))
    return {
        "ok": True,
        "authentication_mode": "internal_integration",
        "project_root": str(root),
        "token_source": resolved_backend,
        "storage": resolved_backend,
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "bot_owner_type": bot_owner_type,
        "storage_options": storage_options,
        "recommended_next_action": "notion_search_resources",
        "keyring_backend": keychain_backend_name(),
    }


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    tc = _token_context(project_root)
    root = Path(tc["project_root"])
    session = tc["session"] or {}
    bp = build_list_bindings_payload(root)
    resources = bp.get("resources") if isinstance(bp.get("resources"), list) else []
    storage_options = _available_storage_backends()
    available = [b["backend"] for b in storage_options if b.get("available")]
    storage_default = (
        DEFAULT_PERSISTENT_STORAGE if DEFAULT_PERSISTENT_STORAGE in available else None
    )
    secret_plan = _secret_plan(token_context=tc, storage_default=storage_default)
    authenticated = bool(tc["token"])
    likely_headless = likely_headless_environment()
    if not authenticated:
        rec_action = "notion_prepare_internal_integration"
    elif resources:
        rec_action = "notion_get_api_context"
    else:
        rec_action = "notion_search_resources"
    return {
        "integration": KEYRING_SERVICE_NAME,
        "project_root": str(root),
        "authentication_mode": "internal_integration",
        "authenticated": authenticated,
        "token_source": tc["token_source"],
        "storage": tc["storage"],
        "env_token_present": tc["env_token_present"],
        "keyring_backend": keychain_backend_name(),
        "keyring_error": tc["keyring_error"],
        "storage_error": tc["storage_error"],
        "storage_options": storage_options,
        "storage_default": storage_default,
        "secret_plan": secret_plan,
        "storage_choice_required": False,
        "workspace_name": session.get("workspace_name"),
        "workspace_id": session.get("workspace_id"),
        "bot_id": session.get("bot_id"),
        "bot_owner_type": session.get("bot_owner_type"),
        "configured_at": session.get("configured_at"),
        "session_path": str(session_path(root)),
        "session_exists": load_project_session(root) is not None,
        "bindings_path": str(bindings_path(root)),
        "bindings_configured": bool(resources),
        "bindings_count": len(resources),
        "recommended_action": rec_action,
        "likely_headless": likely_headless,
        "setup_resource_uri": SETUP_GUIDE_RESOURCE_URI,
        "available_env_var": TOKEN_ENV_VAR,
        "recommended_local_command": f"uvx agent-labbook configure-secret --storage {storage_default}"
        if storage_default
        else None,
        "notion_integrations_url": DEFAULT_NOTION_INTEGRATIONS_URL,
        "notion_docs_url": NOTION_INTEGRATION_GUIDE_URL,
        "notion_version": DEFAULT_NOTION_VERSION,
    }


def project_status_resource(project_root: str | Path | None = None) -> str:
    return json.dumps(
        status(project_root), ensure_ascii=False, indent=2, sort_keys=True
    )


# ---------------------------------------------------------------------------
# clear / api context
# ---------------------------------------------------------------------------


def clear_project_auth(
    *, project_root: str | Path | None = None, clear_bindings: bool = False
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session = load_project_session(root)
    storage = _configured_storage(session)
    deleted = False
    if storage == "keychain":
        deleted = keychain_delete_token(session)
    session_cleared = clear_project_session(root)
    bindings_cleared = clear_project_bindings(root) if clear_bindings else False
    return {
        "ok": True,
        "project_root": str(root),
        "storage": storage,
        "session_cleared": session_cleared,
        "stored_secret_deleted": deleted,
        "bindings_cleared": bindings_cleared,
        "env_token_still_present": bool(
            str(os.environ.get(TOKEN_ENV_VAR) or "").strip()
        ),
    }


def get_api_context(project_root: str | Path | None = None) -> dict[str, Any]:
    tc = _require_token_context(project_root)
    bp = build_list_bindings_payload(tc["project_root"])
    session = tc["session"] or {}
    token = str(tc["token"])
    return {
        "project_root": str(tc["project_root"]),
        "authentication_mode": "internal_integration",
        "token_source": str(tc["token_source"]),
        "storage": tc["storage"],
        "access_token": token,
        "api_base_url": NOTION_API_BASE,
        "notion_version": DEFAULT_NOTION_VERSION,
        "headers": {
            "Authorization": f"Bearer {token}",
            "Notion-Version": DEFAULT_NOTION_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"AgentLabbook/{__version__} (+https://github.com/binbinsh/agent-labbook)",
        },
        "workspace_name": session.get("workspace_name"),
        "workspace_id": session.get("workspace_id"),
        "bot_id": session.get("bot_id"),
        "bound_resources": bp["resources"],
        "selection_scope_note": "selection_scope='subtree' means the resource is treated as a root and nested content is implicitly included.",
    }
