from __future__ import annotations

import logging
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
import webbrowser

logger = logging.getLogger("labbook.auth_flow")

import keyring
from keyring.errors import KeyringError


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


SETUP_GUIDE_RESOURCE_URI = "labbook://agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/status{?project_root}"
BINDINGS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/bindings{?project_root}"
DEFAULT_NOTION_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"
NOTION_INTEGRATION_GUIDE_URL = (
    "https://developers.notion.com/guides/get-started/create-a-notion-integration"
)
DEFAULT_1PASSWORD_ITEM_TITLE = "Notion Agent Labbook Internal Integration Secret"
OP_TIMEOUT_SECONDS = 10
SUPPORTED_STORAGE_BACKENDS = ("keychain", "1password")


def setup_guide() -> str:
    return "\n".join(
        [
            "# Notion Agent Labbook Internal Integration Setup",
            "",
            "Notion Agent Labbook uses a Notion Internal Integration secret directly. There is no OAuth broker, browser handoff, or hosted Worker in this version.",
            "",
            "## Recommended Flow",
            "",
            "1. Run `notion_prepare_internal_integration` to open the Notion integrations dashboard and inspect available secret storage backends.",
            "2. Create or open an Internal Integration in the Notion dashboard.",
            "3. Copy the Internal Integration Secret from the `Configuration` tab.",
            "4. Choose a storage backend: system keychain or 1Password.",
            "5. Store the secret with `notion_configure_internal_integration`, or provide it via the `NOTION_AGENT_LABBOOK_TOKEN` environment variable.",
            "6. Share the target Notion pages or data sources with the integration bot inside Notion.",
            "7. If you already know the exact Notion links, use `notion_bind_resource_urls`.",
            "8. On desktop machines, use `notion_open_binding_browser` for a local chooser.",
            "9. In headless environments, use `notion_search_resources`, `notion_discover_children`, and `notion_bind_resources`.",
            "10. Call `notion_get_api_context` only when you are ready to use the official Notion API.",
            "",
            "## Security Notes",
            "",
            "- `NOTION_AGENT_LABBOOK_TOKEN` takes precedence over any locally stored secret for the current process.",
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


def _keyring_backend_name() -> str:
    try:
        return keyring.get_keyring().__class__.__name__
    except Exception:  # noqa: BLE001
        return "unknown"


def open_browser_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001
        return False


def _keychain_backend_status() -> dict[str, Any]:
    try:
        backend = keyring.get_keyring()
        backend_name = backend.__class__.__name__
        backend_module = backend.__class__.__module__
        priority = getattr(backend, "priority", None)
    except Exception as exc:  # noqa: BLE001
        return {
            "backend": "keychain",
            "display_name": "System Keychain",
            "available": False,
            "selected_by_default": False,
            "reason": f"Could not initialize keyring: {exc}",
            "details": {"keyring_backend": "unknown"},
        }

    available = not str(backend_module).startswith("keyring.backends.fail")
    if isinstance(priority, (int, float)):
        available = available and priority > 0

    return {
        "backend": "keychain",
        "display_name": "System Keychain",
        "available": available,
        "selected_by_default": False,
        "reason": None
        if available
        else "The configured Python keyring backend is unavailable.",
        "details": {
            "keyring_backend": backend_name,
            "keyring_module": backend_module,
        },
    }


def _op_command(
    arguments: list[str],
    *,
    input_text: str | None = None,
    expect_json: bool = False,
) -> tuple[Any | None, str | None]:
    op_path = shutil.which("op")
    if not op_path:
        return None, "The 1Password CLI (`op`) is not installed."

    command = [op_path, *arguments]
    if expect_json:
        if command and command[-1] == "-":
            command = command[:-1] + ["--format", "json", "-"]
        else:
            command.extend(["--format", "json"])

    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=OP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"Could not run 1Password CLI: {exc}"

    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "1Password CLI command failed."
        ).strip()
        return None, message

    output = (result.stdout or "").strip()
    if not expect_json:
        return output, None

    try:
        return json.loads(output or "null"), None
    except json.JSONDecodeError as exc:
        return None, f"1Password CLI returned invalid JSON: {exc}"


def _op_item_reference_from_details(payload: dict[str, Any]) -> str | None:
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict):
            continue
        if (
            field.get("id") == "password"
            or str(field.get("label") or "").strip().lower() == "password"
        ):
            reference = str(field.get("reference") or "").strip()
            if reference:
                return reference
    return None


def _onepassword_backend_status() -> dict[str, Any]:
    op_path = shutil.which("op")
    if not op_path:
        return {
            "backend": "1password",
            "display_name": "1Password",
            "available": False,
            "selected_by_default": False,
            "reason": "The 1Password CLI (`op`) is not installed.",
            "details": {"cli_path": None, "signed_in": False, "vaults": []},
        }

    accounts_payload, accounts_error = _op_command(
        ["account", "list"], expect_json=True
    )
    if accounts_error:
        return {
            "backend": "1password",
            "display_name": "1Password",
            "available": False,
            "selected_by_default": False,
            "reason": accounts_error,
            "details": {"cli_path": op_path, "signed_in": False, "vaults": []},
        }

    accounts = accounts_payload if isinstance(accounts_payload, list) else []
    if not accounts:
        return {
            "backend": "1password",
            "display_name": "1Password",
            "available": False,
            "selected_by_default": False,
            "reason": "No 1Password account is signed in on this machine.",
            "details": {"cli_path": op_path, "signed_in": False, "vaults": []},
        }

    vaults_payload, vaults_error = _op_command(["vault", "list"], expect_json=True)
    vaults: list[dict[str, str]] = []
    if isinstance(vaults_payload, list):
        for item in vaults_payload:
            if not isinstance(item, dict):
                continue
            vault_id = str(item.get("id") or "").strip()
            vault_name = str(item.get("name") or "").strip()
            if vault_id or vault_name:
                vaults.append({"id": vault_id, "name": vault_name})

    return {
        "backend": "1password",
        "display_name": "1Password",
        "available": True,
        "selected_by_default": False,
        "reason": None,
        "details": {
            "cli_path": op_path,
            "signed_in": True,
            "accounts_count": len(accounts),
            "vaults": vaults,
            "vaults_error": vaults_error,
        },
    }


def _recommended_storage_backend(backends: list[dict[str, Any]]) -> str | None:
    available = [item["backend"] for item in backends if item.get("available")]
    if "keychain" in available:
        return "keychain"
    if "1password" in available:
        return "1password"
    return None


def _available_storage_backends() -> list[dict[str, Any]]:
    backends = [_keychain_backend_status(), _onepassword_backend_status()]
    recommended = _recommended_storage_backend(backends)
    for backend in backends:
        backend["selected_by_default"] = backend.get("backend") == recommended
    return backends


def _resolve_storage_backend(
    requested_backend: str | None = None,
    *,
    available_backends: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    backends = available_backends or _available_storage_backends()
    available = {item["backend"]: item for item in backends if item.get("available")}
    clean_requested = str(requested_backend or "auto").strip().lower() or "auto"

    if clean_requested not in {"auto", *SUPPORTED_STORAGE_BACKENDS}:
        raise LabbookError("storage must be one of: auto, keychain, 1password.")

    if clean_requested == "auto":
        if len(available) == 1:
            return next(iter(available.keys())), backends
        if len(available) > 1:
            options = ", ".join(sorted(available))
            raise LabbookError(
                f"Multiple secret storage backends are available ({options}). "
                "Ask the user to choose one explicitly and pass storage."
            )
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


def _store_token_in_keyring(*, project_root: Path, token: str) -> tuple[str, str]:
    service_name = KEYRING_SERVICE_NAME
    account = f"project-root:{project_root}"
    try:
        keyring.set_password(service_name, account, token)
    except KeyringError as exc:
        raise LabbookError(
            "Could not store the Notion integration secret in the local keyring. "
            f"Set {TOKEN_ENV_VAR} as an environment variable if you cannot use keyring on this machine."
        ) from exc
    return service_name, account


def _store_token_in_onepassword(
    *,
    project_root: Path,
    token: str,
    vault: str | None = None,
    item_title: str | None = None,
) -> dict[str, str | None]:
    title = str(
        item_title or f"{DEFAULT_1PASSWORD_ITEM_TITLE} ({project_root.name})"
    ).strip()
    if not title:
        title = DEFAULT_1PASSWORD_ITEM_TITLE

    template = {
        "title": title,
        "category": "PASSWORD",
        "fields": [
            {
                "id": "password",
                "type": "CONCEALED",
                "purpose": "PASSWORD",
                "label": "password",
                "value": token,
            },
            {
                "id": "notesPlain",
                "type": "STRING",
                "purpose": "NOTES",
                "label": "notesPlain",
                "value": (
                    f"Stored by {KEYRING_SERVICE_NAME} for project {project_root}. "
                    "Contains the Notion Internal Integration Secret."
                ),
            },
        ],
    }

    create_arguments = ["item", "create"]
    if vault:
        create_arguments.extend(["--vault", vault])
    create_arguments.extend(
        ["--tags", "agent-labbook,notion,internal-integration", "-"]
    )
    created_payload, create_error = _op_command(
        create_arguments,
        input_text=json.dumps(template, ensure_ascii=False),
        expect_json=True,
    )
    if create_error or not isinstance(created_payload, dict):
        raise LabbookError(
            f"Could not store the Notion integration secret in 1Password. {create_error or 'Unknown error.'}"
        )

    item_id = str(created_payload.get("id") or "").strip()
    if not item_id:
        raise LabbookError("1Password did not return the created item ID.")

    created_vault = created_payload.get("vault")
    created_vault_id = (
        str(created_vault.get("id") or "").strip()
        if isinstance(created_vault, dict)
        else ""
    )
    created_vault_name = (
        str(created_vault.get("name") or "").strip()
        if isinstance(created_vault, dict)
        else ""
    )
    item_details_arguments = ["item", "get", item_id]
    item_scope = created_vault_id or created_vault_name or str(vault or "").strip()
    if item_scope:
        item_details_arguments.extend(["--vault", item_scope])
    item_details_payload, item_details_error = _op_command(
        item_details_arguments, expect_json=True
    )
    if item_details_error or not isinstance(item_details_payload, dict):
        raise LabbookError(
            f"1Password created the item but its details could not be read back. {item_details_error or 'Unknown error.'}"
        )

    reference = _op_item_reference_from_details(item_details_payload)
    details_vault = item_details_payload.get("vault")
    vault_id = created_vault_id or (
        str(details_vault.get("id") or "").strip()
        if isinstance(details_vault, dict)
        else ""
    )
    vault_name = created_vault_name or (
        str(details_vault.get("name") or "").strip()
        if isinstance(details_vault, dict)
        else ""
    )
    if not reference and (vault_id or vault_name):
        reference = f"op://{vault_id or vault_name}/{item_id}/password"

    return {
        "op_item_id": item_id or None,
        "op_item_title": str(item_details_payload.get("title") or title).strip()
        or title,
        "op_vault": vault_name or str(vault or "").strip() or None,
        "op_vault_id": vault_id or None,
        "op_ref": reference or None,
    }


def _delete_token_from_keyring(session_payload: dict[str, Any] | None) -> bool:
    if not isinstance(session_payload, dict):
        return False
    service_name = str(session_payload.get("keyring_service") or "").strip()
    account = str(session_payload.get("keyring_account") or "").strip()
    if not service_name or not account:
        return False
    try:
        keyring.delete_password(service_name, account)
    except KeyringError:
        return False
    return True


def _delete_token_from_onepassword(session_payload: dict[str, Any] | None) -> bool:
    if not isinstance(session_payload, dict):
        return False
    item_id = str(session_payload.get("op_item_id") or "").strip()
    if not item_id:
        return False
    arguments = ["item", "delete", item_id]
    vault = str(
        session_payload.get("op_vault_id") or session_payload.get("op_vault") or ""
    ).strip()
    if vault:
        arguments.extend(["--vault", vault])
    _, error = _op_command(arguments, expect_json=False)
    return error is None


def _keyring_token(
    session_payload: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if not isinstance(session_payload, dict):
        return None, None
    service_name = str(session_payload.get("keyring_service") or "").strip()
    account = str(session_payload.get("keyring_account") or "").strip()
    if not service_name or not account:
        return None, None
    try:
        token = keyring.get_password(service_name, account)
    except KeyringError as exc:
        return None, str(exc)
    if not token:
        return (
            None,
            "The stored Notion integration secret could not be found in keyring.",
        )
    return token, None


def _onepassword_token(
    session_payload: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if not isinstance(session_payload, dict):
        return None, None
    reference = str(session_payload.get("op_ref") or "").strip()
    if not reference:
        item_id = str(session_payload.get("op_item_id") or "").strip()
        vault = str(
            session_payload.get("op_vault_id") or session_payload.get("op_vault") or ""
        ).strip()
        if item_id and vault:
            reference = f"op://{vault}/{item_id}/password"
    if not reference:
        return None, "The stored 1Password reference is missing."
    token, error = _op_command(["read", reference], expect_json=False)
    if error:
        return None, error
    clean_token = str(token or "").strip()
    if not clean_token:
        return (
            None,
            "The stored Notion integration secret could not be read from 1Password.",
        )
    return clean_token, None


def _configured_storage(session_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(session_payload, dict):
        return None
    clean_value = str(session_payload.get("storage") or "").strip().lower()
    if clean_value in {"keychain", "1password"}:
        return clean_value
    return None


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
        "Run notion_prepare_internal_integration and notion_configure_internal_integration, "
        f"or set {TOKEN_ENV_VAR}."
    )


def notion_client_for_project(
    project_root: str | Path | None = None,
) -> tuple[NotionClient, dict[str, Any]]:
    context = _require_token_context(project_root)
    return NotionClient(token=str(context["token"])), context


def prepare_internal_integration(
    *,
    project_root: str | Path | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    storage_options = _available_storage_backends()
    storage_default = _recommended_storage_backend(storage_options)
    browser_opened = (
        open_browser_url(DEFAULT_NOTION_INTEGRATIONS_URL) if open_browser else False
    )
    setup_steps = [
        "Create or open a Notion Internal Integration in the dashboard.",
        "Copy the Internal Integration Secret from the Configuration tab.",
        "Choose a storage backend and save the secret with notion_configure_internal_integration.",
        "Share the required pages or data sources with the integration bot in Notion.",
    ]
    return {
        "project_root": str(root),
        "notion_integrations_url": DEFAULT_NOTION_INTEGRATIONS_URL,
        "notion_docs_url": NOTION_INTEGRATION_GUIDE_URL,
        "browser_opened": browser_opened,
        "open_browser_attempted": bool(open_browser),
        "storage_options": storage_options,
        "storage_default": storage_default,
        "storage_choice_required": sum(
            1 for item in storage_options if item.get("available")
        )
        > 1,
        "secret_label": "Internal Integration Secret",
        "setup_steps": setup_steps,
        "recommended_next_action": "notion_configure_internal_integration",
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

    resolved_backend, storage_options = _resolve_storage_backend(storage)
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
        "available storage backends, then run notion_configure_internal_integration, "
        f"or set {TOKEN_ENV_VAR}."
    )


def _storage_hint(storage_options: list[dict[str, Any]]) -> str:
    available_names = [
        item["backend"] for item in storage_options if item.get("available")
    ]
    if not available_names:
        return f"No supported local storage backend is available. Use {TOKEN_ENV_VAR}."
    if len(available_names) == 1:
        return f"{available_names[0]} is the only available local storage backend on this machine."
    return (
        "More than one local storage backend is available. Ask the user whether they want "
        "system keychain or 1Password, then pass storage explicitly."
    )


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
        "storage_choice_required": sum(
            1 for item in storage_options if item.get("available")
        )
        > 1,
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
        "notion_integrations_url": DEFAULT_NOTION_INTEGRATIONS_URL,
        "notion_docs_url": NOTION_INTEGRATION_GUIDE_URL,
        "notion_version": DEFAULT_NOTION_VERSION,
    }


def project_status_resource(project_root: str | Path | None = None) -> str:
    return json.dumps(
        status(project_root), ensure_ascii=False, indent=2, sort_keys=True
    )


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
