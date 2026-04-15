from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import platform
import sys
import warnings
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

from . import __version__
from .auth_flow import configure_internal_integration, status
from .state import (
    TOKEN_ENV_VAR,
    bindings_path,
    load_project_bindings,
    load_project_session,
    resolve_project_root,
    session_path,
)

logger = logging.getLogger("labbook.cli")

__all__ = ["build_parser", "main"]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _json_dump(payload: dict[str, Any]) -> None:
    """Pretty-print *payload* as JSON to stdout."""
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _installed_mcp_sdk_version() -> str | None:
    """Return the installed ``mcp`` package version, or ``None``."""
    try:
        return package_version("mcp")
    except PackageNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Secret prompting
# ---------------------------------------------------------------------------


def _prompt_for_secret_once(prompt: str) -> str:
    """Read a secret from the terminal without echo.

    Raises :class:`RuntimeError` when the terminal cannot suppress echo
    or the user cancels input.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning as exc:
        raise RuntimeError(
            "Could not read the secret securely from a local terminal. "
            "Run this command in an interactive shell with a real TTY."
        ) from exc
    except (EOFError, KeyboardInterrupt) as exc:
        raise RuntimeError("Secret entry cancelled.") from exc


def _prompt_for_secret_twice() -> str:
    """Prompt for, confirm, and return the Notion integration secret."""
    secret = _prompt_for_secret_once("Notion Internal Integration Secret: ").strip()
    if not secret:
        raise RuntimeError("Secret cannot be empty.")

    confirmed = _prompt_for_secret_once("Confirm Secret: ").strip()
    if secret != confirmed:
        raise RuntimeError("Secret confirmation did not match.")

    return secret


# ---------------------------------------------------------------------------
# MCP config snippet
# ---------------------------------------------------------------------------


def _mcp_server_config(*, server_name: str) -> dict[str, Any]:
    """Return a reusable ``mcpServers`` config snippet for ``uvx``."""
    return {
        "mcpServers": {
            server_name: {
                "command": "uvx",
                "args": ["agent-labbook", "mcp"],
            }
        }
    }


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _doctor_command(args: argparse.Namespace) -> int:
    """Inspect local Notion Agent Labbook state and print diagnostics."""
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
            "token_env_present": bool(os.environ.get(TOKEN_ENV_VAR)),
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
    _json_dump(payload)
    return 0


def _print_mcp_config_command(args: argparse.Namespace) -> int:
    """Print a reusable uvx-based MCP server config snippet."""
    _json_dump(_mcp_server_config(server_name=args.server_name))
    return 0


def _configure_secret_command(args: argparse.Namespace) -> int:
    """Prompt for the Notion secret and store it locally."""
    project_root = resolve_project_root(args.project_root)
    secret = _prompt_for_secret_twice()
    payload = configure_internal_integration(
        secret=secret,
        project_root=project_root,
        storage=args.storage,
        op_vault=args.op_vault,
        op_item_title=args.op_item_title,
    )
    _json_dump(payload)
    return 0


def _run_mcp_command() -> int:
    """Launch the MCP stdio server."""
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


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="agent-labbook",
        description="Notion Agent Labbook CLI and MCP launcher.",
    )
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers(dest="command")

    # --- mcp ---
    mcp_parser = subparsers.add_parser("mcp", help="Run the MCP stdio server.")
    mcp_parser.set_defaults(func=lambda _args: _run_mcp_command())

    # --- doctor ---
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

    # --- print-mcp-config ---
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

    # --- configure-secret ---
    secret_parser = subparsers.add_parser(
        "configure-secret",
        help="Prompt for the Notion Internal Integration secret and store it locally.",
    )
    secret_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root to configure. Defaults to the current directory.",
    )
    secret_parser.add_argument(
        "--storage",
        default="auto",
        choices=["auto", "keychain", "1password"],
        help=(
            "Local storage backend for the secret. "
            "'auto' prefers keychain when available and otherwise "
            "falls back to 1Password."
        ),
    )
    secret_parser.add_argument(
        "--op-vault",
        default=None,
        help="Optional 1Password vault name or ID.",
    )
    secret_parser.add_argument(
        "--op-item-title",
        default=None,
        help="Optional 1Password item title.",
    )
    secret_parser.set_defaults(func=_configure_secret_command)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the appropriate subcommand."""
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
