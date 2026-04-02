from __future__ import annotations

import argparse
import json
import platform
import sys
from typing import Any
from urllib import error, request

from . import __version__
from .service import status
from .state import (
    DEFAULT_BACKEND_URL,
    bindings_path,
    effective_backend_url,
    load_project_bindings,
    load_project_session,
    pending_auth_path,
    resolve_project_root,
    session_path,
)


def _json_dump(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _probe_backend_health(backend_url: str) -> dict[str, Any]:
    req = request.Request(
        f"{backend_url}/health",
        headers={"Accept": "application/json"},
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
                "command": "python3",
                "args": ["scripts/run_labbook.py", "mcp"],
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
        },
        "notion_status": status(project_root),
        "mcp": {
            "install_surface": "codex mcp add / claude mcp add / project .mcp.json",
            "launcher": _mcp_server_config(server_name="labbook")["mcpServers"]["labbook"],
            "sdk": "modelcontextprotocol/python-sdk",
            "transport": "stdio",
            "wire_protocol": "content-length",
        },
    }
    if args.probe_backend:
        payload["backend_probe"] = _probe_backend_health(backend_url)
    _json_dump(payload)
    return 0


def _print_mcp_config_command(args: argparse.Namespace) -> int:
    _json_dump(_mcp_server_config(server_name=args.server_name))
    return 0


def _run_mcp_command() -> int:
    try:
        from .mcp_server import main as mcp_main
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            raise RuntimeError(
                "The Agent Labbook MCP runtime is not installed in this Python environment. "
                "Launch the server through scripts/run_labbook.py so it can bootstrap the official MCP SDK automatically."
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
        help="Print a reusable project-level MCP config snippet.",
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
