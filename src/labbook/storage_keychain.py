"""Keychain (system keyring) storage backend for Notion integration secrets.

Provides functions to store, retrieve, delete, and inspect the health of
the local system keychain via the ``keyring`` library.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

from .state import KEYRING_SERVICE_NAME, TOKEN_ENV_VAR, LabbookError

logger = logging.getLogger("labbook.storage_keychain")

__all__ = [
    "backend_name",
    "backend_status",
    "store_token",
    "retrieve_token",
    "delete_token",
]


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def backend_name() -> str:
    """Return the human-readable class name of the active keyring backend."""
    try:
        return keyring.get_keyring().__class__.__name__
    except Exception:  # noqa: BLE001
        return "unknown"


def backend_status() -> dict[str, Any]:
    """Return a status dict describing keychain availability."""
    try:
        backend = keyring.get_keyring()
        backend_cls = backend.__class__.__name__
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
            "keyring_backend": backend_cls,
            "keyring_module": backend_module,
        },
    }


# ---------------------------------------------------------------------------
# Store / retrieve / delete
# ---------------------------------------------------------------------------


def store_token(*, project_root: Path, token: str) -> tuple[str, str]:
    """Store *token* in the system keychain; return ``(service, account)``."""
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


def retrieve_token(
    session_payload: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return ``(token, error)`` from keychain using *session_payload*."""
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


def delete_token(session_payload: dict[str, Any] | None) -> bool:
    """Delete the keychain entry described by *session_payload*; return success."""
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
