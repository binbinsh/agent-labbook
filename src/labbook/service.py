from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
import webbrowser

try:
    import keyring
    from keyring.errors import KeyringError
except ModuleNotFoundError:  # pragma: no cover - exercised in local import environments
    keyring = None

    class KeyringError(RuntimeError):
        pass


from . import __version__
from .notion_api import NOTION_API_BASE, NotionClient
from .state import (
    DEFAULT_NOTION_VERSION,
    KEYRING_SERVICE_NAME,
    TOKEN_ENV_VAR,
    LabbookError,
    bindings_path,
    clear_project_bindings,
    clear_project_session,
    load_project_bindings,
    load_project_session,
    normalize_notion_id,
    resolve_project_root,
    save_project_bindings,
    save_project_session,
    session_path,
)


CLIENT_USER_AGENT = f"NotionAgentLabbook/{__version__} (+https://github.com/binbinsh/notion-agent-labbook)"
SETUP_GUIDE_RESOURCE_URI = "labbook://notion-agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://notion-agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://notion-agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = (
    "labbook://notion-agent-labbook/project/status{?project_root}"
)
BINDINGS_RESOURCE_TEMPLATE = (
    "labbook://notion-agent-labbook/project/bindings{?project_root}"
)
DEFAULT_SEARCH_PAGE_SIZE = 25
MIN_SEARCH_PAGE_SIZE = 1
MAX_SEARCH_PAGE_SIZE = 100
DEFAULT_NOTION_INTEGRATIONS_URL = "https://www.notion.so/my-integrations"
NOTION_INTEGRATION_GUIDE_URL = (
    "https://developers.notion.com/guides/get-started/create-a-notion-integration"
)
DEFAULT_1PASSWORD_ITEM_TITLE = "Notion Agent Labbook Internal Integration Secret"
OP_TIMEOUT_SECONDS = 10
SUPPORTED_STORAGE_BACKENDS = ("keychain", "1password")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_search_page_size(page_size: int | str | None = None) -> int:
    if page_size in (None, ""):
        return DEFAULT_SEARCH_PAGE_SIZE
    try:
        limit = int(page_size)
    except (TypeError, ValueError) as exc:
        raise LabbookError("page_size must be an integer number of results.") from exc
    return min(max(limit, MIN_SEARCH_PAGE_SIZE), MAX_SEARCH_PAGE_SIZE)


def _rich_text_to_plain_text(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    text = "".join(
        str(item.get("plain_text") or "") for item in items if isinstance(item, dict)
    ).strip()
    return text or None


def _resource_title(resource: dict[str, Any]) -> str | None:
    object_type = str(resource.get("object") or "").strip().lower()
    properties = resource.get("properties")
    if object_type == "page":
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
        if isinstance(properties, dict):
            for value in properties.values():
                if isinstance(value, dict) and value.get("type") == "title":
                    title = _rich_text_to_plain_text(value.get("title"))
                    if title:
                        return title
    if object_type in {"data_source", "database"}:
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
    title = str(resource.get("name") or "").strip()
    return title or None


def _resource_type(resource: dict[str, Any]) -> str:
    raw = (
        str(resource.get("object") or resource.get("resource_type") or "")
        .strip()
        .lower()
    )
    if raw == "database":
        return "data_source"
    if raw in {"page", "data_source"}:
        return raw
    return "unknown"


def _endpoint_for_resource(resource_type: str | None, resource_id: str) -> str | None:
    clean_id = normalize_notion_id(resource_id)
    if resource_type == "page":
        return f"{NOTION_API_BASE}/pages/{clean_id}"
    if resource_type == "data_source":
        return f"{NOTION_API_BASE}/data_sources/{clean_id}"
    return None


def _slugify_alias(text: str, *, fallback: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return candidate or fallback


def _normalize_selection_scope(value: Any) -> str:
    clean_value = str(value or "resource").strip().lower()
    if clean_value not in {"resource", "subtree"}:
        raise LabbookError("selection_scope must be 'resource' or 'subtree'.")
    return clean_value


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    if len(resources) != 1:
        return None
    return str(resources[0].get("alias") or "").strip() or None


def _build_setup_guide() -> str:
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
            "7. Use `notion_search_resources` to discover accessible content, then `notion_bind_resources` to bind the pages or data sources this project should use.",
            "8. Call `notion_get_api_context` only when you are ready to use the official Notion API.",
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


def setup_guide() -> str:
    return _build_setup_guide()


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
        "token_source": storage,
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
        "configured_at": _utc_now(),
        "notion_version": DEFAULT_NOTION_VERSION,
    }


def _keyring_account(project_root: Path) -> str:
    return f"project-root:{project_root}"


def _keyring_backend_name() -> str:
    if keyring is None:
        return "unavailable"
    try:
        return keyring.get_keyring().__class__.__name__
    except Exception:  # noqa: BLE001
        return "unknown"


def _open_browser_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001
        return False


def _keychain_backend_status() -> dict[str, Any]:
    if keyring is None:
        return {
            "backend": "keychain",
            "display_name": "System Keychain",
            "available": False,
            "selected_by_default": False,
            "reason": "The Python 'keyring' package is not installed.",
            "details": {"keyring_backend": "unavailable"},
        }

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
    if keyring is None:
        raise LabbookError(
            "The Python 'keyring' package is not installed. "
            f"Set {TOKEN_ENV_VAR} as an environment variable if you cannot install keyring."
        )
    service_name = KEYRING_SERVICE_NAME
    account = _keyring_account(project_root)
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
        [
            "--tags",
            "notion-agent-labbook,notion,internal-integration",
            "-",
        ]
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
    if keyring is None:
        return False
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
    if keyring is None:
        return None, "The Python 'keyring' package is not installed."
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
    clean_value = (
        str(session_payload.get("storage") or session_payload.get("token_source") or "")
        .strip()
        .lower()
    )
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


def _notion_client(
    project_root: str | Path | None = None,
) -> tuple[NotionClient, dict[str, Any]]:
    context = _require_token_context(project_root)
    return NotionClient(token=str(context["token"])), context


def _normalize_notion_resource(resource: dict[str, Any]) -> dict[str, Any] | None:
    resource_type = _resource_type(resource)
    if resource_type not in {"page", "data_source"}:
        return None
    resource_id = normalize_notion_id(
        str(resource.get("id") or resource.get("resource_id") or "")
    )
    title = _resource_title(resource) or resource_id
    resource_url = (
        str(resource.get("url") or resource.get("resource_url") or "").strip() or None
    )
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resource_url": resource_url,
        "title": title,
        "last_edited_time": resource.get("last_edited_time"),
        "parent": resource.get("parent"),
    }


def _normalize_binding_entry(
    *,
    resource_id: str,
    resource_type: str,
    resource_url: str | None,
    title: str | None,
    alias: str | None,
    selection_scope: str | None = None,
    source: str = "manual_bind",
    bound_at: str | None = None,
) -> dict[str, Any]:
    clean_id = normalize_notion_id(resource_id)
    clean_type = str(resource_type or "").strip().lower()
    if clean_type == "database":
        clean_type = "data_source"
    if clean_type not in {"page", "data_source"}:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    clean_title = str(title or clean_id).strip() or clean_id
    clean_alias = _slugify_alias(alias or clean_title, fallback=clean_type)
    clean_url = str(resource_url or "").strip() or _endpoint_for_resource(
        clean_type, clean_id
    )
    return {
        "alias": clean_alias,
        "resource_id": clean_id,
        "resource_type": clean_type,
        "resource_url": clean_url,
        "title": clean_title,
        "source": source,
        "bound_at": bound_at or _utc_now(),
        "selection_scope": _normalize_selection_scope(selection_scope),
    }


def _existing_bindings(project_root: Path) -> list[dict[str, Any]]:
    bindings_payload = load_project_bindings(project_root) or {}
    resources = bindings_payload.get("resources")
    if not isinstance(resources, list):
        return []
    return [item for item in resources if isinstance(item, dict)]


def _alias_for_resource(
    *,
    resource_id: str,
    proposed_alias: str,
    used_aliases: set[str],
    alias_owner: dict[str, str],
) -> str:
    if (
        proposed_alias not in used_aliases
        or alias_owner.get(proposed_alias) == resource_id
    ):
        used_aliases.add(proposed_alias)
        alias_owner[proposed_alias] = resource_id
        return proposed_alias

    suffix = 2
    while True:
        candidate = f"{proposed_alias}-{suffix}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            alias_owner[candidate] = resource_id
            return candidate
        suffix += 1


def _sorted_bindings(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        resources,
        key=lambda item: (
            str(item.get("title") or "").lower(),
            str(item.get("resource_type") or "").lower(),
            str(item.get("resource_id") or "").lower(),
        ),
    )


def _bindings_payload(
    project_root: Path, resources: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "default_resource_alias": _default_resource_alias(resources),
        "resources": _sorted_bindings(resources),
    }


def prepare_internal_integration(
    *,
    project_root: str | Path | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    storage_options = _available_storage_backends()
    storage_default = _recommended_storage_backend(storage_options)
    browser_opened = (
        _open_browser_url(DEFAULT_NOTION_INTEGRATIONS_URL) if open_browser else False
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
    else:  # pragma: no cover - resolved backend is validated above
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


def search_resources(
    *,
    project_root: str | Path | None = None,
    query: str | None = None,
    page_size: int | str | None = None,
) -> dict[str, Any]:
    page_limit = normalize_search_page_size(page_size)
    client, context = _notion_client(project_root)
    payload = client.search(query=query, page_size=page_limit)
    results: list[dict[str, Any]] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_notion_resource(item)
        if normalized is not None:
            results.append(normalized)
    return {
        "project_root": str(context["project_root"]),
        "query": str(query or "").strip() or None,
        "page_size": page_limit,
        "result_count": len(results),
        "results": results,
    }


def _normalize_resource_input(item: dict[str, Any]) -> dict[str, str | None]:
    resource_ref = str(
        item.get("resource_id_or_url")
        or item.get("resource_id")
        or item.get("resource_url")
        or ""
    ).strip()
    if not resource_ref:
        raise LabbookError(
            "Each resource_refs item must include resource_id_or_url, resource_id, or resource_url."
        )
    resource_id = normalize_notion_id(resource_ref)
    resource_type = str(item.get("resource_type") or "").strip().lower() or None
    if resource_type == "database":
        resource_type = "data_source"
    if resource_type not in {None, "page", "data_source"}:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    alias = str(item.get("alias") or "").strip() or None
    title = str(item.get("title") or "").strip() or None
    selection_scope = _normalize_selection_scope(item.get("selection_scope"))
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resource_url": str(item.get("resource_url") or "").strip() or None,
        "alias": alias,
        "title": title,
        "selection_scope": selection_scope,
    }


def bind_resources(
    *,
    resource_refs: list[dict[str, Any]],
    project_root: str | Path | None = None,
    default_alias: str | None = None,
) -> dict[str, Any]:
    if not resource_refs:
        raise LabbookError("resource_refs must contain at least one resource.")

    client, context = _notion_client(project_root)
    root = Path(context["project_root"])
    existing_resources = _existing_bindings(root)
    by_resource_id = {
        str(item.get("resource_id")): dict(item)
        for item in existing_resources
        if isinstance(item.get("resource_id"), str)
    }

    for index, raw_item in enumerate(resource_refs):
        if not isinstance(raw_item, dict):
            raise LabbookError("Each item in resource_refs must be an object.")
        normalized_input = _normalize_resource_input(raw_item)
        resource = client.retrieve_resource(
            normalized_input["resource_id"] or "",
            normalized_input["resource_type"],
        )
        normalized_resource = _normalize_notion_resource(resource)
        if normalized_resource is None:
            raise LabbookError(
                f"Resource {normalized_input['resource_id']} is not a page or data source."
            )

        fallback_alias = (
            default_alias if len(resource_refs) == 1 and default_alias else None
        )
        previous = by_resource_id.get(normalized_resource["resource_id"])
        by_resource_id[normalized_resource["resource_id"]] = _normalize_binding_entry(
            resource_id=normalized_resource["resource_id"],
            resource_type=normalized_input["resource_type"]
            or normalized_resource["resource_type"],
            resource_url=normalized_input["resource_url"]
            or normalized_resource["resource_url"],
            title=normalized_input["title"] or normalized_resource["title"],
            alias=normalized_input["alias"]
            or fallback_alias
            or (previous or {}).get("alias")
            or f"resource-{index + 1}",
            selection_scope=normalized_input["selection_scope"],
            source="manual_bind",
            bound_at=(previous or {}).get("bound_at"),
        )

    final_resources = _sorted_bindings(list(by_resource_id.values()))
    used_aliases: set[str] = set()
    alias_owner: dict[str, str] = {}
    for resource in final_resources:
        resource["alias"] = _alias_for_resource(
            resource_id=str(resource["resource_id"]),
            proposed_alias=_slugify_alias(
                str(resource["alias"]), fallback=str(resource["resource_type"])
            ),
            used_aliases=used_aliases,
            alias_owner=alias_owner,
        )

    payload = _bindings_payload(root, final_resources)
    save_project_bindings(root, payload)
    return {
        "project_root": str(root),
        "default_resource_alias": payload["default_resource_alias"],
        "resource_count": len(final_resources),
        "resources": final_resources,
    }


def list_bindings(project_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    payload = load_project_bindings(root) or _bindings_payload(root, [])
    resources = payload.get("resources")
    if not isinstance(resources, list):
        resources = []
    return {
        "project_root": str(root),
        "default_resource_alias": payload.get("default_resource_alias"),
        "resource_count": len(resources),
        "resources": resources,
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
        backend = available_names[0]
        return f"{backend} is the only available local storage backend on this machine."
    return (
        "More than one local storage backend is available. Ask the user whether they want "
        "system keychain or 1Password, then pass storage explicitly."
    )


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
    bindings_payload = load_project_bindings(root) or _bindings_payload(root, [])
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
            "Use notion_search_resources to find pages or data sources the bot can access, then bind them with notion_bind_resources."
            if authenticated and not resources
            else "Read notion_list_bindings before making API calls."
            if resources
            else "No Notion secret is configured yet."
        ),
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


def project_bindings_resource(project_root: str | Path | None = None) -> str:
    return json.dumps(
        list_bindings(project_root), ensure_ascii=False, indent=2, sort_keys=True
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
    bindings_payload = list_bindings(token_context["project_root"])
    session_payload = token_context["session"] or {}
    token = str(token_context["token"])
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
            "User-Agent": CLIENT_USER_AGENT,
        },
        "workspace_name": session_payload.get("workspace_name"),
        "workspace_id": session_payload.get("workspace_id"),
        "bot_id": session_payload.get("bot_id"),
        "bound_resources": bindings_payload["resources"],
        "selection_scope_note": (
            "selection_scope='subtree' means the resource is treated as a root and nested content is implicitly included."
        ),
    }
