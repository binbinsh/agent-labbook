from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import UUID


DEFAULT_BACKEND_URL = "https://superplanner.ai/notion/agent-labbook"
DEFAULT_OAUTH_BASE_URL = "https://superplanner.ai/notion/oauth"
INTEGRATION_ID = "agent-labbook"
DEFAULT_NOTION_VERSION = "2026-03-11"
PROJECT_STATE_DIRNAME = ".labbook"
SESSION_FILENAME = "session.json"
BINDINGS_FILENAME = "bindings.json"
PENDING_AUTH_FILENAME = "pending-auth.json"
PENDING_HANDOFF_FILENAME = "pending-handoff.json"
LOCAL_HANDOFF_SERVER_FILENAME = "local-handoff-server.json"
SESSION_STATE_VERSION = 1
BINDINGS_STATE_VERSION = 1
PENDING_AUTH_STATE_VERSION = 1
PENDING_HANDOFF_STATE_VERSION = 1
LOCAL_HANDOFF_SERVER_STATE_VERSION = 1


class LabbookError(RuntimeError):
    pass


_STATE_SCHEMA_SPECS = {
    SESSION_FILENAME: {
        "label": "Project session state",
        "version": SESSION_STATE_VERSION,
        "inject_integration": True,
    },
    BINDINGS_FILENAME: {
        "label": "Project bindings state",
        "version": BINDINGS_STATE_VERSION,
        "inject_integration": False,
    },
    PENDING_AUTH_FILENAME: {
        "label": "Pending auth state",
        "version": PENDING_AUTH_STATE_VERSION,
        "inject_integration": True,
    },
    PENDING_HANDOFF_FILENAME: {
        "label": "Pending handoff state",
        "version": PENDING_HANDOFF_STATE_VERSION,
        "inject_integration": False,
    },
    LOCAL_HANDOFF_SERVER_FILENAME: {
        "label": "Local handoff server state",
        "version": LOCAL_HANDOFF_SERVER_STATE_VERSION,
        "inject_integration": False,
    },
}


def _normalize_http_url(value: str, *, env_name: str) -> str:
    if not value:
        raise LabbookError(f"{env_name} cannot be empty.")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LabbookError(f"Invalid backend URL: {value!r}")
    if parsed.params or parsed.query or parsed.fragment:
        raise LabbookError(f"{env_name} must not include params, query, or fragment.")
    normalized_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def effective_backend_url() -> str:
    value = str(os.getenv("AGENT_LABBOOK_BACKEND_URL") or DEFAULT_BACKEND_URL).strip()
    return _normalize_http_url(value, env_name="AGENT_LABBOOK_BACKEND_URL")


def effective_oauth_base_url() -> str:
    value = str(os.getenv("AGENT_LABBOOK_OAUTH_BASE_URL") or DEFAULT_OAUTH_BASE_URL).strip()
    return _normalize_http_url(value, env_name="AGENT_LABBOOK_OAUTH_BASE_URL")


def oauth_callback_uri(oauth_base_url: str | None = None) -> str:
    return f"{oauth_base_url or effective_oauth_base_url()}/callback"


def backend_redirect_uri(backend_url: str | None = None) -> str:
    return oauth_callback_uri(backend_url)


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


def _state_schema_spec(path: Path) -> dict[str, object] | None:
    return _STATE_SCHEMA_SPECS.get(path.name)


def _normalize_state_payload(
    path: Path,
    payload: dict,
    *,
    for_save: bool,
) -> dict:
    spec = _state_schema_spec(path)
    if spec is None:
        return dict(payload)

    label = str(spec["label"])
    current_version = int(spec["version"])
    inject_integration = bool(spec["inject_integration"])
    normalized = dict(payload)
    version_raw = normalized.get("version")

    if version_raw in (None, ""):
        version = 0
    else:
        try:
            version = int(version_raw)
        except (TypeError, ValueError) as exc:
            raise LabbookError(f"{label} has an invalid version field: {version_raw!r}.") from exc

    if for_save:
        if version not in {0, current_version}:
            raise LabbookError(
                f"{label} cannot be saved with version {version}. Current version is {current_version}."
            )
    else:
        if version > current_version:
            raise LabbookError(
                f"{label} uses unsupported future version {version}. Current version is {current_version}."
            )
        if version not in {0, current_version}:
            raise LabbookError(
                f"{label} uses unsupported version {version}. Current version is {current_version}."
            )

    normalized["version"] = current_version
    if inject_integration:
        integration = str(normalized.get("integration") or INTEGRATION_ID).strip() or INTEGRATION_ID
        if integration != INTEGRATION_ID:
            raise LabbookError(
                f"{label} belongs to integration {integration!r}, but this project expects {INTEGRATION_ID!r}."
            )
        normalized["integration"] = integration
    return normalized


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabbookError(f"Invalid JSON in {path}") from exc
    if not isinstance(payload, dict):
        raise LabbookError(f"Expected an object in {path}")
    return _normalize_state_payload(path, payload, for_save=False)


def _save_json(path: Path, payload: dict) -> Path:
    normalized_payload = _normalize_state_payload(path, payload, for_save=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    path.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
