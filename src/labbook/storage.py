"""Secret storage backend: the local system keychain."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

from .state import KEYRING_SERVICE_NAME, TOKEN_ENV_VAR, LabbookError

logger = logging.getLogger("labbook.storage")

# Default timeout for probing whether the keychain backend is usable. Probes
# run on every `notion_status` / `notion_open_binding_browser` /
# `notion_search_resources` call, so they must stay snappy even when the
# underlying Secret Service / keychain daemon is unhealthy. A hang here would
# otherwise let a Codex MCP client close the stdio transport before we reply.
# Override via `LABBOOK_PROBE_TIMEOUT_SECONDS`.
_DEFAULT_PROBE_TIMEOUT_SECONDS = 3.0


def _probe_timeout_default() -> float:
    raw = os.environ.get("LABBOOK_PROBE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_PROBE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PROBE_TIMEOUT_SECONDS
    return max(0.25, value)


def _call_with_timeout(func, *, timeout: float, default: Any) -> tuple[Any, bool]:
    """Run ``func()`` in a daemon thread. Return (result, timed_out)."""
    result: dict[str, Any] = {"value": default}
    error: dict[str, BaseException | None] = {"exc": None}

    def _runner() -> None:
        try:
            result["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - we re-raise below
            error["exc"] = exc

    thread = threading.Thread(target=_runner, name="labbook-probe", daemon=True)
    thread.start()
    thread.join(timeout=max(0.0, timeout))
    if thread.is_alive():
        return default, True
    if error["exc"] is not None:
        raise error["exc"]  # type: ignore[misc]
    return result["value"], False


# ---------------------------------------------------------------------------
# Keychain backend
# ---------------------------------------------------------------------------


def keychain_backend_name() -> str:
    try:
        value, timed_out = _call_with_timeout(
            lambda: keyring.get_keyring().__class__.__name__,
            timeout=_probe_timeout_default(),
            default="unknown",
        )
    except Exception:
        return "unknown"
    if timed_out:
        return "unknown"
    return value or "unknown"


def keychain_backend_status(*, probe_timeout: float | None = None) -> dict[str, Any]:
    timeout = probe_timeout if probe_timeout is not None else _probe_timeout_default()
    try:
        probe_result, timed_out = _call_with_timeout(
            lambda: (
                keyring.get_keyring().__class__.__name__,
                keyring.get_keyring().__class__.__module__,
                getattr(keyring.get_keyring(), "priority", None),
            ),
            timeout=timeout,
            default=None,
        )
    except Exception as exc:
        return {
            "backend": "keychain",
            "display_name": "System Keychain",
            "available": False,
            "selected_by_default": False,
            "reason": f"Could not initialize keyring: {exc}",
            "details": {"keyring_backend": "unknown"},
        }
    if timed_out or probe_result is None:
        return {
            "backend": "keychain",
            "display_name": "System Keychain",
            "available": False,
            "selected_by_default": False,
            "reason": (
                "Keyring backend probe timed out after "
                f"{timeout:.1f}s. The local Secret Service / keychain may be "
                "locked or unresponsive."
            ),
            "details": {"keyring_backend": "unknown", "probe_timed_out": True},
        }
    backend_cls, backend_module, priority = probe_result
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
        "details": {"keyring_backend": backend_cls, "keyring_module": backend_module},
    }


def keychain_store_token(*, project_root: Path, token: str) -> tuple[str, str]:
    service_name = KEYRING_SERVICE_NAME
    account = f"project-root:{project_root}"
    try:
        keyring.set_password(service_name, account, token)
    except KeyringError as exc:
        raise LabbookError(
            f"Could not store the Notion integration secret in the local keyring. "
            f"Set {TOKEN_ENV_VAR} as an environment variable if you cannot use keyring on this machine."
        ) from exc
    return service_name, account


def keychain_retrieve_token(
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


def keychain_delete_token(session_payload: dict[str, Any] | None) -> bool:
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
