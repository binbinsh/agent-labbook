"""1Password storage backend for Notion integration secrets.

Provides functions to store, retrieve, delete, and inspect the health of
1Password via the ``op`` CLI.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .state import KEYRING_SERVICE_NAME, LabbookError

logger = logging.getLogger("labbook.storage_op")

__all__ = [
    "DEFAULT_1PASSWORD_ITEM_TITLE",
    "OP_TIMEOUT_SECONDS",
    "op_command",
    "backend_status",
    "store_token",
    "retrieve_token",
    "delete_token",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_1PASSWORD_ITEM_TITLE = "Notion Agent Labbook Internal Integration Secret"
OP_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Low-level CLI helper
# ---------------------------------------------------------------------------


def op_command(
    arguments: list[str],
    *,
    input_text: str | None = None,
    expect_json: bool = False,
) -> tuple[Any | None, str | None]:
    """Run an ``op`` CLI command; return ``(payload, error)``."""
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


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def _item_reference_from_details(payload: dict[str, Any]) -> str | None:
    """Extract the ``op://`` reference for the password field."""
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


def backend_status() -> dict[str, Any]:
    """Return a status dict describing 1Password availability."""
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

    accounts_payload, accounts_error = op_command(["account", "list"], expect_json=True)
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

    vaults_payload, vaults_error = op_command(["vault", "list"], expect_json=True)
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


# ---------------------------------------------------------------------------
# Store / retrieve / delete
# ---------------------------------------------------------------------------


def store_token(
    *,
    project_root: Path,
    token: str,
    vault: str | None = None,
    item_title: str | None = None,
) -> dict[str, str | None]:
    """Create a 1Password Password item; return session metadata dict."""
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
    created_payload, create_error = op_command(
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
    item_details_payload, item_details_error = op_command(
        item_details_arguments, expect_json=True
    )
    if item_details_error or not isinstance(item_details_payload, dict):
        raise LabbookError(
            f"1Password created the item but its details could not be read back. {item_details_error or 'Unknown error.'}"
        )

    reference = _item_reference_from_details(item_details_payload)
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


def retrieve_token(
    session_payload: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return ``(token, error)`` from 1Password using *session_payload*."""
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
    token, error = op_command(["read", reference], expect_json=False)
    if error:
        return None, error
    clean_token = str(token or "").strip()
    if not clean_token:
        return (
            None,
            "The stored Notion integration secret could not be read from 1Password.",
        )
    return clean_token, None


def delete_token(session_payload: dict[str, Any] | None) -> bool:
    """Delete the 1Password item described by *session_payload*; return success."""
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
    _, error = op_command(arguments, expect_json=False)
    return error is None
