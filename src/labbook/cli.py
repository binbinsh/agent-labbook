from __future__ import annotations

import argparse
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any
from urllib import error, request

from . import __version__
from .service import status
from .state import (
    DEFAULT_BACKEND_URL,
    bindings_path,
    effective_backend_url,
    local_handoff_server_path,
    load_project_bindings,
    load_project_session,
    pending_auth_path,
    pending_handoff_path,
    resolve_project_root,
    session_path,
)

CLIENT_USER_AGENT = f"AgentLabbook/{__version__} (+https://github.com/binbinsh/agent-labbook)"


def _json_dump(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _installed_mcp_sdk_version() -> str | None:
    try:
        return package_version("mcp")
    except PackageNotFoundError:
        return None


def _probe_backend_health(backend_url: str) -> dict[str, Any]:
    req = request.Request(
        f"{backend_url}/health",
        headers={
            "Accept": "application/json",
            "User-Agent": CLIENT_USER_AGENT,
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
            status_code = response.status
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status_code": exc.code,
            "error": raw or exc.reason,
        }
    except error.URLError as exc:
        return {
            "ok": False,
            "error": str(exc.reason),
        }

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = {"raw": raw}
    return {
        "ok": True,
        "status_code": status_code,
        "payload": decoded,
    }


def _mcp_server_config(*, server_name: str) -> dict[str, Any]:
    return {
        "mcpServers": {
            server_name: {
                "command": "uvx",
                "args": ["agent-labbook", "mcp"],
            }
        }
    }


def _doctor_command(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    backend_url = effective_backend_url()
    pending_path = pending_auth_path(project_root)
    payload: dict[str, Any] = {
        "version": __version__,
        "python": {
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "project_root": str(project_root),
        "backend_url": backend_url,
        "backend_url_overridden": backend_url != DEFAULT_BACKEND_URL,
        "state": {
            "session_path": str(session_path(project_root)),
            "session_exists": load_project_session(project_root) is not None,
            "bindings_path": str(bindings_path(project_root)),
            "bindings_exists": load_project_bindings(project_root) is not None,
            "pending_auth_path": str(pending_path),
            "pending_auth_exists": pending_path.exists(),
            "pending_handoff_path": str(pending_handoff_path(project_root)),
            "pending_handoff_exists": pending_handoff_path(project_root).exists(),
            "local_handoff_server_path": str(local_handoff_server_path(project_root)),
            "local_handoff_server_exists": local_handoff_server_path(project_root).exists(),
        },
        "notion_status": status(project_root),
        "mcp": {
            "install_surface": "uvx agent-labbook mcp",
            "sdk": "modelcontextprotocol/python-sdk",
            "sdk_version": _installed_mcp_sdk_version(),
            "transport": "stdio",
            "wire_protocol": "content-length",
        },
    }
    if args.probe_backend:
        payload["backend_probe"] = _probe_backend_health(backend_url)
    _json_dump(payload)
    return 0


def _print_mcp_config_command(args: argparse.Namespace) -> int:
    payload = _mcp_server_config(server_name=args.server_name)
    _json_dump(payload)
    return 0


def _run_mcp_command() -> int:
    try:
        from .mcp_server import main as mcp_main
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            raise RuntimeError(
                "The Python 'mcp' package is not installed. "
                "Use 'uvx agent-labbook mcp'."
            ) from exc
        raise

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-labbook",
        description="Agent Labbook CLI and MCP launcher.",
    )
    parser.set_defaults(func=None)

    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP stdio server.")
    mcp_parser.set_defaults(func=lambda _args: _run_mcp_command())

    doctor_parser = subparsers.add_parser("doctor", help="Inspect local Agent Labbook state.")
    doctor_parser.add_argument("--project-root", default=".", help="Project root to inspect. Defaults to the current directory.")
    doctor_parser.add_argument(
        "--probe-backend",
        action="store_true",
        help="Also query the configured backend /health endpoint.",
    )
    doctor_parser.set_defaults(func=_doctor_command)

    config_parser = subparsers.add_parser(
        "print-mcp-config",
        help="Print a reusable uvx-based MCP server config snippet.",
    )
    config_parser.add_argument(
        "--server-name",
        default="labbook",
        help="Name to use under mcpServers.",
    )
    config_parser.set_defaults(func=_print_mcp_config_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        parser.print_help(sys.stderr)
        return 1
    try:
        return int(args.func(args) or 0)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
