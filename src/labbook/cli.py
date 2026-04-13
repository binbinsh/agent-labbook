from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

logger = logging.getLogger("labbook.cli")

from . import __version__
from .auth_flow import status
from .state import (
    TOKEN_ENV_VAR,
    bindings_path,
    load_project_bindings,
    load_project_session,
    resolve_project_root,
    session_path,
)


def _json_dump(payload: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _installed_mcp_sdk_version() -> str | None:
    try:
        return package_version("mcp")
    except PackageNotFoundError:
        return None


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
    status_payload = status(project_root)
    payload: dict[str, Any] = {
        "version": __version__,
        "python": {
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "project_root": str(project_root),
        "state": {
            "session_path": str(session_path(project_root)),
            "session_exists": load_project_session(project_root) is not None,
            "bindings_path": str(bindings_path(project_root)),
            "bindings_exists": load_project_bindings(project_root) is not None,
        },
        "environment": {
            "token_env_var": TOKEN_ENV_VAR,
            "token_env_present": False,
        },
        "secret_plan": status_payload.get("secret_plan"),
        "notion_status": status_payload,
        "mcp": {
            "install_surface": "uvx agent-labbook mcp",
            "sdk": "modelcontextprotocol/python-sdk",
            "sdk_version": _installed_mcp_sdk_version(),
            "transport": "stdio",
            "wire_protocol": "content-length",
        },
    }
    payload["environment"]["token_env_present"] = bool(os.environ.get(TOKEN_ENV_VAR))
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
        description="Notion Agent Labbook CLI and MCP launcher.",
    )
    parser.set_defaults(func=None)

    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP stdio server.")
    mcp_parser.set_defaults(func=lambda _args: _run_mcp_command())

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect local Notion Agent Labbook state.",
    )
    doctor_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root to inspect. Defaults to the current directory.",
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
