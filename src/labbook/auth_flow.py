"""Authentication and project-session management for Notion Agent Labbook.

Public API (imported elsewhere):
    setup_guide, status, project_status_resource,
    prepare_internal_integration, configure_internal_integration,
    clear_project_auth, get_api_context,
    notion_client_for_project, open_browser_url,
    SETUP_GUIDE_RESOURCE_URI, STATUS_RESOURCE_URI, BINDINGS_RESOURCE_URI,
    STATUS_RESOURCE_TEMPLATE, BINDINGS_RESOURCE_TEMPLATE.

Storage backends are delegated to :mod:`storage_keychain` and :mod:`storage_op`.
"""

from __future__ import annotations

import json
import logging
import os
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .binding_ops import build_list_bindings_payload
from .notion_api import NOTION_API_BASE, NotionClient
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
from .storage_keychain import (
    backend_name as _keyring_backend_name,
    backend_status as _keychain_backend_status,
    delete_token as _delete_token_from_keyring,
    retrieve_token as _keyring_token,
    store_token as _store_token_in_keyring,
)
from .storage_op import (
    backend_status as _onepassword_backend_status,
    delete_token as _delete_token_from_onepassword,
    retrieve_token as _onepassword_token,
    store_token as _store_token_in_onepassword,
)

logger = logging.getLogger("labbook.auth_flow")


# ---------------------------------------------------------------------------
# Resource URIs
# ---------------------------------------------------------------------------

SETUP_GUIDE_RESOURCE_URI = "labbook://agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/status{?project_root}"
BINDINGS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/bindings{?project_root}"

# ---------------------------------------------------------------------------
# External URLs / defaults
# ---------------------------------------------------------------------------

DEFAULT_NOTION_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"
NOTION_INTEGRATION_GUIDE_URL = (
    "https://developers.notion.com/guides/get-started/create-a-notion-integration"
)
SUPPORTED_STORAGE_BACKENDS = ("keychain", "1password")
DEFAULT_PERSISTENT_STORAGE = "keychain"


# ---------------------------------------------------------------------------
# Setup guide
# ---------------------------------------------------------------------------


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
            "Use `--storage 1password` only when you explicitly want 1Password.",
            f"Use `{TOKEN_ENV_VAR}` only for CI or temporary overrides.",
            "",
            "## Security Notes",
            "",
            "- `NOTION_AGENT_LABBOOK_TOKEN` takes precedence over any locally stored secret for the current process and is intended for CI or temporary overrides.",
            "- `agent-labbook configure-secret` uses a local hidden prompt so the secret does not need to be pasted into chat or shell history.",
            "- When you choose `keychain`, the secret is stored in the local system credential store through Python `keyring`.",
            "- When you choose `1password`, the secret is stored as a 1Password Password item and retrieved later with `op read`.",
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


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _session_payload(
    *,
    project_root: Path,
    storage: str,
    workspace_name: str | None,
    workspace_id: str | None,
    bot_id: str | None,
    bot_owner_type: str | None,
    keyring_service: str | None = None,
    keyring_account: str | None = None,
    op_item_id: str | None = None,
    op_item_title: str | None = None,
    op_vault: str | None = None,
    op_vault_id: str | None = None,
    op_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "storage": storage,
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "bot_owner_type": bot_owner_type,
        "keyring_service": keyring_service,
        "keyring_account": keyring_account,
        "op_item_id": op_item_id,
        "op_item_title": op_item_title,
        "op_vault": op_vault,
        "op_vault_id": op_vault_id,
        "op_ref": op_ref,
        "configured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notion_version": DEFAULT_NOTION_VERSION,
    }


def open_browser_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Storage resolution
# ---------------------------------------------------------------------------


def _recommended_storage_backend(backends: list[dict[str, Any]]) -> str | None:
    available = [item["backend"] for item in backends if item.get("available")]
    if DEFAULT_PERSISTENT_STORAGE in available:
        return DEFAULT_PERSISTENT_STORAGE
    if "1password" in available:
        return "1password"
    return None


def _available_storage_backends() -> list[dict[str, Any]]:
    backends = [_keychain_backend_status(), _onepassword_backend_status()]
    recommended = _recommended_storage_backend(backends)
    for backend in backends:
        backend["selected_by_default"] = backend.get("backend") == recommended
    return backends


def _storage_choice_required(backends: list[dict[str, Any]]) -> bool:
    available = [item["backend"] for item in backends if item.get("available")]
    if not available:
        return False
    return _recommended_storage_backend(backends) is None and len(available) > 1


def _recommended_local_command(storage_default: str | None) -> str | None:
    if storage_default == "keychain":
        return "uvx agent-labbook configure-secret --storage keychain"
    if storage_default == "1password":
        return "uvx agent-labbook configure-secret --storage 1password"
    return None


def _resolve_storage_backend(
    requested_backend: str | None = None,
    *,
    available_backends: list[dict[str, Any]] | None = None,
    current_backend: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    backends = available_backends or _available_storage_backends()
    available = {item["backend"]: item for item in backends if item.get("available")}
    clean_requested = str(requested_backend or "auto").strip().lower() or "auto"
    clean_current = str(current_backend or "").strip().lower() or None

    if clean_requested not in {"auto", *SUPPORTED_STORAGE_BACKENDS}:
        raise LabbookError("storage must be one of: auto, keychain, 1password.")

    if clean_requested == "auto":
        if clean_current in available:
            return clean_current, backends
        recommended = _recommended_storage_backend(backends)
        if recommended:
            return recommended, backends
        raise LabbookError(
            f"No supported secret storage backends are available. Set {TOKEN_ENV_VAR} instead."
        )

    if clean_requested not in available:
        reason = None
        for backend in backends:
            if backend.get("backend") == clean_requested:
                reason = backend.get("reason")
                break
        suffix = f" {reason}" if reason else ""
        raise LabbookError(f"storage={clean_requested!r} is not available.{suffix}")

    return clean_requested, backends


def _configured_storage(session_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(session_payload, dict):
        return None
    clean_value = str(session_payload.get("storage") or "").strip().lower()
    if clean_value in {"keychain", "1password"}:
        return clean_value
    return None


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _token_context(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root)
    env_token = str(os.environ.get(TOKEN_ENV_VAR) or "").strip()
    configured_backend = _configured_storage(session_payload)
    if env_token:
        return {
            "project_root": root,
            "session": session_payload,
            "token": env_token,
            "token_source": "env",
            "storage": configured_backend,
            "env_token_present": True,
            "keyring_error": None,
            "onepassword_error": None,
            "storage_error": None,
        }

    keyring_error = None
    onepassword_error = None
    token: str | None = None
    token_source: str | None = None

    if configured_backend == "keychain":
        token, keyring_error = _keyring_token(session_payload)
        token_source = "keychain" if token else None
    elif configured_backend == "1password":
        token, onepassword_error = _onepassword_token(session_payload)
        token_source = "1password" if token else None

    return {
        "project_root": root,
        "session": session_payload,
        "token": token,
        "token_source": token_source,
        "storage": configured_backend,
        "env_token_present": False,
        "keyring_error": keyring_error,
        "onepassword_error": onepassword_error,
        "storage_error": keyring_error or onepassword_error,
    }


def _require_token_context(project_root: str | Path | None = None) -> dict[str, Any]:
    context = _token_context(project_root)
    if context["token"]:
        return context
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
    context = _require_token_context(project_root)
    return NotionClient(token=str(context["token"])), context


# ---------------------------------------------------------------------------
# prepare / configure
# ---------------------------------------------------------------------------


def prepare_internal_integration(
    *,
    project_root: str | Path | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    storage_options = _available_storage_backends()
    storage_default = _recommended_storage_backend(storage_options)
    recommended_local_command = _recommended_local_command(storage_default)
    browser_opened = (
        open_browser_url(DEFAULT_NOTION_INTEGRATIONS_URL) if open_browser else False
    )
    setup_steps = [
        "Create a Notion Internal Integration and share the target pages or data sources with it.",
        (
            f"Run `{recommended_local_command}` for a local hidden prompt."
            if recommended_local_command
            else "If no local secret backend is available, use NOTION_AGENT_LABBOOK_TOKEN as a process-scoped override."
        ),
        "Bind the resources you want to use.",
        "Call notion_get_api_context only when you are ready to use the official Notion API.",
    ]
    return {
        "project_root": str(root),
        "notion_integrations_url": DEFAULT_NOTION_INTEGRATIONS_URL,
        "notion_docs_url": NOTION_INTEGRATION_GUIDE_URL,
        "browser_opened": browser_opened,
        "open_browser_attempted": bool(open_browser),
        "storage_options": storage_options,
        "storage_default": storage_default,
        "storage_choice_required": _storage_choice_required(storage_options),
        "secret_label": "Internal Integration Secret",
        "setup_steps": setup_steps,
        "recommended_next_action": "notion_configure_internal_integration",
        "recommended_local_command": recommended_local_command,
    }


def configure_internal_integration(
    *,
    secret: str,
    project_root: str | Path | None = None,
    storage: str | None = None,
    op_vault: str | None = None,
    op_item_title: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    clean_secret = str(secret or "").strip()
    if not clean_secret:
        raise LabbookError("secret is required.")

    session_payload = load_project_session(root)
    resolved_backend, storage_options = _resolve_storage_backend(
        storage,
        current_backend=_configured_storage(session_payload),
    )
    client = NotionClient(token=clean_secret)
    me = client.get_me()
    bot = me.get("bot") if isinstance(me.get("bot"), dict) else {}
    owner = bot.get("owner") if isinstance(bot.get("owner"), dict) else {}
    workspace_name = str(bot.get("workspace_name") or "").strip() or None
    workspace_id = str(bot.get("workspace_id") or "").strip() or None
    bot_id = str(me.get("id") or "").strip() or None
    bot_owner_type = str(owner.get("type") or "").strip() or None

    session_kwargs: dict[str, Any] = {
        "project_root": root,
        "storage": resolved_backend,
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "bot_owner_type": bot_owner_type,
    }

    if resolved_backend == "keychain":
        keyring_service, keyring_account = _store_token_in_keyring(
            project_root=root, token=clean_secret
        )
        session_kwargs["keyring_service"] = keyring_service
        session_kwargs["keyring_account"] = keyring_account
    elif resolved_backend == "1password":
        session_kwargs.update(
            _store_token_in_onepassword(
                project_root=root,
                token=clean_secret,
                vault=op_vault,
                item_title=op_item_title,
            )
        )
    else:  # pragma: no cover
        raise LabbookError(f"Unsupported storage backend: {resolved_backend}")

    save_project_session(root, _session_payload(**session_kwargs))
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
        "op_vault": session_kwargs.get("op_vault"),
        "op_item_title": session_kwargs.get("op_item_title"),
        "keyring_backend": _keyring_backend_name(),
    }


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


def _authentication_hint(token_context: dict[str, Any]) -> str:
    if token_context["token_source"] == "env":
        return f"Using {TOKEN_ENV_VAR} from the environment for this process."
    if token_context["token_source"] == "keychain":
        return (
            "Using the Internal Integration secret stored in the local system keychain."
        )
    if token_context["token_source"] == "1password":
        return "Using the Internal Integration secret stored in 1Password."
    return (
        "Run notion_prepare_internal_integration to open the Notion integrations dashboard and inspect "
        "available storage backends, then prefer agent-labbook configure-secret "
        "(which defaults to keychain when available), "
        "or run notion_configure_internal_integration when you can safely provide the secret directly, "
        f"or set {TOKEN_ENV_VAR}."
    )


def _storage_hint(storage_options: list[dict[str, Any]]) -> str:
    storage_default = _recommended_storage_backend(storage_options)
    if storage_default == "keychain":
        return (
            "System keychain is the default persistent backend on this machine. "
            "Use storage='1password' only when you explicitly want 1Password."
        )
    if storage_default == "1password":
        return "1Password is the only supported persistent backend currently available on this machine."
    if not any(item.get("available") for item in storage_options):
        return f"No supported local storage backend is available. Use {TOKEN_ENV_VAR}."
    return "No preferred local storage backend could be determined."


def _binding_option(
    *,
    mode: str,
    label: str,
    available: bool,
    recommended: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "label": label,
        "available": available,
        "recommended": recommended,
        "reason": reason,
    }


def _likely_headless_environment() -> bool:
    for env_name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "CI"):
        if str(os.environ.get(env_name) or "").strip():
            return True
    return False


def _binding_recommendation(
    *,
    authenticated: bool,
    likely_headless: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not authenticated:
        reason = "Finish configuring the Internal Integration secret first. Binding choices matter only after Notion auth succeeds."
        recommendation = {"mode": "wait_for_auth", "reason": reason}
        options = [
            _binding_option(
                mode="url",
                label="Paste exact links",
                available=False,
                recommended=False,
                reason="Requires an authenticated Notion integration before binding can succeed.",
            ),
            _binding_option(
                mode="local_browser",
                label="Open local chooser",
                available=False,
                recommended=False,
                reason="Requires an authenticated Notion integration before binding can succeed.",
            ),
            _binding_option(
                mode="manual_mcp",
                label="Search in chat",
                available=False,
                recommended=False,
                reason="Requires an authenticated Notion integration before binding can succeed.",
            ),
        ]
        return recommendation, options

    if likely_headless:
        recommendation = {
            "mode": "url",
            "reason": "This environment looks headless, so asking for exact Notion links first is the lowest-friction path.",
        }
        options = [
            _binding_option(
                mode="url",
                label="Paste exact links",
                available=True,
                recommended=True,
                reason="Best default for headless or SSH sessions.",
            ),
            _binding_option(
                mode="local_browser",
                label="Open local chooser",
                available=False,
                recommended=False,
                reason="A local browser chooser is not the default in headless environments.",
            ),
            _binding_option(
                mode="manual_mcp",
                label="Search in chat",
                available=True,
                recommended=False,
                reason="Use search plus child discovery when the user cannot provide exact links.",
            ),
        ]
        return recommendation, options

    recommendation = {
        "mode": "local_browser",
        "reason": "This environment looks desktop-capable, so the local browser chooser is the best default for picking from many pages and child pages.",
    }
    options = [
        _binding_option(
            mode="url",
            label="Paste exact links",
            available=True,
            recommended=False,
            reason="Fastest path when the user already knows the exact Notion URLs.",
        ),
        _binding_option(
            mode="local_browser",
            label="Open local chooser",
            available=True,
            recommended=True,
            reason="Best default on desktop when the user needs to search and inspect nested content.",
        ),
        _binding_option(
            mode="manual_mcp",
            label="Search in chat",
            available=True,
            recommended=False,
            reason="Useful fallback when the browser chooser is inconvenient or unavailable.",
        ),
    ]
    return recommendation, options


def _binding_question(*, likely_headless: bool) -> str:
    if likely_headless:
        return "Can you paste one or more exact Notion links? If not, I can search and narrow the tree here in chat."
    return "Can you paste one or more exact Notion links, or do you want me to open the local chooser?"


def _secret_plan(
    *,
    token_context: dict[str, Any],
    storage_default: str | None,
) -> tuple[str, str]:
    configured_storage = token_context.get("storage")
    token_source = token_context.get("token_source")
    env_token_present = bool(token_context.get("env_token_present"))

    if (
        configured_storage in {"keychain", "1password"}
        and token_source == configured_storage
    ):
        if configured_storage == "keychain":
            return (
                "keychain",
                "This project is already using the local system keychain successfully, so keeping that backend is the least disruptive option.",
            )
        return (
            "1password",
            "This project is already using 1Password successfully, so keeping that backend is the least disruptive option.",
        )

    if storage_default == "keychain":
        reason = "System keychain is the default recommendation for persistent local development on this machine."
        if env_token_present:
            reason += f" The current process is using {TOKEN_ENV_VAR}, but keychain is the better long-lived default."
        return "keychain", reason

    if storage_default == "1password":
        reason = "1Password is the only supported local secret backend detected, so it is the best persistent option here."
        if env_token_present:
            reason += f" The current process is using {TOKEN_ENV_VAR}, but 1Password is the better long-lived default."
        return "1password", reason

    return (
        "env",
        f"No supported local secret backend is available, so {TOKEN_ENV_VAR} is the recommended option.",
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status(project_root: str | Path | None = None) -> dict[str, Any]:
    token_context = _token_context(project_root)
    root = Path(token_context["project_root"])
    session_payload = token_context["session"] or {}
    bindings_payload = build_list_bindings_payload(root)
    resources = bindings_payload.get("resources")
    if not isinstance(resources, list):
        resources = []

    storage_options = _available_storage_backends()
    storage_default = _recommended_storage_backend(storage_options)
    recommended_local_command = _recommended_local_command(storage_default)
    secret_plan_mode, secret_plan_reason = _secret_plan(
        token_context=token_context,
        storage_default=storage_default,
    )
    authenticated = bool(token_context["token"])
    likely_headless = _likely_headless_environment()
    binding_recommendation, binding_options = _binding_recommendation(
        authenticated=authenticated,
        likely_headless=likely_headless,
    )
    if not authenticated:
        recommended_action = "notion_prepare_internal_integration"
    elif resources:
        recommended_action = "notion_get_api_context"
    else:
        recommended_action = "notion_search_resources"

    return {
        "integration": KEYRING_SERVICE_NAME,
        "project_root": str(root),
        "authentication_mode": "internal_integration",
        "authenticated": authenticated,
        "token_source": token_context["token_source"],
        "storage": token_context["storage"],
        "env_token_present": token_context["env_token_present"],
        "keyring_backend": _keyring_backend_name(),
        "keyring_error": token_context["keyring_error"],
        "onepassword_error": token_context["onepassword_error"],
        "storage_error": token_context["storage_error"],
        "storage_options": storage_options,
        "storage_default": storage_default,
        "secret_plan": {
            "mode": secret_plan_mode,
            "reason": secret_plan_reason,
        },
        "storage_choice_required": _storage_choice_required(storage_options),
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "bot_owner_type": session_payload.get("bot_owner_type"),
        "configured_at": session_payload.get("configured_at"),
        "session_path": str(session_path(root)),
        "session_exists": load_project_session(root) is not None,
        "bindings_path": str(bindings_path(root)),
        "bindings_configured": bool(resources),
        "bindings_count": len(resources),
        "recommended_action": recommended_action,
        "authentication_hint": _authentication_hint(token_context),
        "storage_hint": _storage_hint(storage_options),
        "binding_hint": (
            "Ask whether the user can paste exact Notion links. If not, default to the local browser chooser on desktop, or use MCP search plus child discovery in headless environments."
            if authenticated and not resources
            else "Read notion_list_bindings before making API calls."
            if resources
            else "No Notion secret is configured yet."
        ),
        "likely_headless": likely_headless,
        "binding_recommendation": binding_recommendation,
        "binding_options": binding_options,
        "binding_question": _binding_question(likely_headless=likely_headless),
        "setup_resource_uri": SETUP_GUIDE_RESOURCE_URI,
        "available_env_var": TOKEN_ENV_VAR,
        "recommended_local_command": recommended_local_command,
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
    *,
    project_root: str | Path | None = None,
    clear_bindings: bool = False,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    session_payload = load_project_session(root)
    storage = _configured_storage(session_payload)
    stored_secret_deleted = False
    if storage == "keychain":
        stored_secret_deleted = _delete_token_from_keyring(session_payload)
    elif storage == "1password":
        stored_secret_deleted = _delete_token_from_onepassword(session_payload)

    session_cleared = clear_project_session(root)
    bindings_cleared = clear_project_bindings(root) if clear_bindings else False
    return {
        "ok": True,
        "project_root": str(root),
        "storage": storage,
        "session_cleared": session_cleared,
        "stored_secret_deleted": stored_secret_deleted,
        "bindings_cleared": bindings_cleared,
        "env_token_still_present": bool(
            str(os.environ.get(TOKEN_ENV_VAR) or "").strip()
        ),
    }


def get_api_context(project_root: str | Path | None = None) -> dict[str, Any]:
    token_context = _require_token_context(project_root)
    bindings_payload = build_list_bindings_payload(token_context["project_root"])
    session_payload = token_context["session"] or {}
    token = str(token_context["token"])
    logger.info(
        "API context requested for project %s (token_source=%s)",
        token_context["project_root"],
        token_context["token_source"],
    )
    return {
        "project_root": str(token_context["project_root"]),
        "authentication_mode": "internal_integration",
        "token_source": str(token_context["token_source"]),
        "storage": token_context["storage"],
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
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "bound_resources": bindings_payload["resources"],
        "selection_scope_note": (
            "selection_scope='subtree' means the resource is treated as a root and nested content is implicitly included."
        ),
    }
