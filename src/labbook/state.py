from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID


DEFAULT_BACKEND_URL = "https://labbook.superplanner.net"
DEFAULT_NOTION_VERSION = "2026-03-11"
PROJECT_STATE_DIRNAME = ".labbook"
SESSION_FILENAME = "session.json"
BINDINGS_FILENAME = "bindings.json"
PENDING_AUTH_FILENAME = "pending-auth.json"
PENDING_HANDOFF_FILENAME = "pending-handoff.json"
LOCAL_HANDOFF_SERVER_FILENAME = "local-handoff-server.json"


class LabbookError(RuntimeError):
    pass


def effective_backend_url() -> str:
    value = str(os.getenv("AGENT_LABBOOK_BACKEND_URL") or DEFAULT_BACKEND_URL).strip()
    if not value:
        raise LabbookError("AGENT_LABBOOK_BACKEND_URL cannot be empty.")
    if not re.match(r"^https?://[^/]+", value):
        raise LabbookError(f"Invalid backend URL: {value!r}")
    return value.rstrip("/")


def backend_redirect_uri(backend_url: str | None = None) -> str:
    return f"{backend_url or effective_backend_url()}/oauth/callback"


def resolve_project_root(project_root: str | Path | None = None) -> Path:
    root = Path(project_root or os.getcwd()).expanduser()
    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise LabbookError(f"Project root does not exist: {root}") from exc
    if not root.is_dir():
        raise LabbookError(f"Project root is not a directory: {root}")
    return root


def project_state_dir(project_root: str | Path | None = None) -> Path:
    return resolve_project_root(project_root) / PROJECT_STATE_DIRNAME


def session_path(project_root: str | Path | None = None) -> Path:
    return project_state_dir(project_root) / SESSION_FILENAME


def bindings_path(project_root: str | Path | None = None) -> Path:
    return project_state_dir(project_root) / BINDINGS_FILENAME


def pending_auth_path(project_root: str | Path | None = None) -> Path:
    return project_state_dir(project_root) / PENDING_AUTH_FILENAME


def pending_handoff_path(project_root: str | Path | None = None) -> Path:
    return project_state_dir(project_root) / PENDING_HANDOFF_FILENAME


def local_handoff_server_path(project_root: str | Path | None = None) -> Path:
    return project_state_dir(project_root) / LOCAL_HANDOFF_SERVER_FILENAME


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabbookError(f"Invalid JSON in {path}") from exc
    if not isinstance(payload, dict):
        raise LabbookError(f"Expected an object in {path}")
    return payload


def _save_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def load_project_session(project_root: str | Path | None = None) -> dict | None:
    return _load_json(session_path(project_root))


def save_project_session(project_root: str | Path | None, payload: dict) -> Path:
    return _save_json(session_path(project_root), payload)


def load_project_bindings(project_root: str | Path | None = None) -> dict | None:
    return _load_json(bindings_path(project_root))


def save_project_bindings(project_root: str | Path | None, payload: dict) -> Path:
    return _save_json(bindings_path(project_root), payload)


def load_pending_auth(project_root: str | Path | None = None) -> dict | None:
    return _load_json(pending_auth_path(project_root))


def save_pending_auth(project_root: str | Path | None, payload: dict) -> Path:
    return _save_json(pending_auth_path(project_root), payload)


def load_pending_handoff(project_root: str | Path | None = None) -> dict | None:
    return _load_json(pending_handoff_path(project_root))


def save_pending_handoff(project_root: str | Path | None, payload: dict) -> Path:
    return _save_json(pending_handoff_path(project_root), payload)


def load_local_handoff_server(project_root: str | Path | None = None) -> dict | None:
    return _load_json(local_handoff_server_path(project_root))


def save_local_handoff_server(project_root: str | Path | None, payload: dict) -> Path:
    return _save_json(local_handoff_server_path(project_root), payload)


def clear_pending_auth(project_root: str | Path | None = None) -> bool:
    path = pending_auth_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_pending_handoff(project_root: str | Path | None = None) -> bool:
    path = pending_handoff_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_local_handoff_server(project_root: str | Path | None = None) -> bool:
    path = local_handoff_server_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_project_session(project_root: str | Path | None = None) -> bool:
    path = session_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def clear_project_bindings(project_root: str | Path | None = None) -> bool:
    path = bindings_path(project_root)
    if not path.exists():
        return False
    path.unlink()
    return True


def normalize_notion_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise LabbookError("Notion resource ID cannot be empty.")

    candidates = re.findall(
        r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        raw,
    )
    for candidate in reversed(candidates):
        try:
            return str(UUID(candidate))
        except ValueError:
            continue

    raise LabbookError(f"Could not find a valid Notion resource ID in {value!r}.")
