from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import __version__
from .service import (
    auth_browser,
    bind_resources,
    clear_project_auth,
    complete_headless_auth,
    get_api_context,
    list_bindings,
    refresh_session,
    setup_guide,
    start_headless_auth,
    status,
)
from .state import LabbookError


ToolHandler = Callable[[dict[str, Any]], dict[str, Any] | str]


def _tool_definitions() -> list[dict[str, Any]]:
    resource_ref_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "resource_id_or_url": {"type": "string"},
            "resource_id": {"type": "string"},
            "resource_url": {"type": "string"},
            "resource_type": {"type": "string"},
            "alias": {"type": "string"},
        },
    }
    return [
        {
            "name": "notion_status",
            "description": "Check whether this project already has a saved Notion public-integration session and which Notion resources are bound locally.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        },
        {
            "name": "notion_setup_guide",
            "description": "Return the setup guide for the hosted public integration and its Cloudflare Worker backend.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "notion_auth_browser",
            "description": "Open the official Notion public-integration consent flow in a browser and wait for the selected bindings to be saved into this project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                    "open_browser": {"type": "boolean"},
                    "page_limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "notion_start_headless_auth",
            "description": "Create a headless public-integration auth URL. The user can finish auth in any browser and then paste the returned handoff bundle back into Codex.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "page_limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "notion_complete_headless_auth",
            "description": "Finish a headless public-integration auth flow using the handoff bundle shown by the Worker callback page.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "handoff_bundle": {"type": "string"},
                },
                "required": ["handoff_bundle"],
            },
        },
        {
            "name": "notion_refresh_session",
            "description": "Refresh the saved Notion public-integration session for this project through the Worker backend.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        },
        {
            "name": "notion_clear_project_auth",
            "description": "Remove the saved project-local Notion session, and optionally the bound resources too.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "clear_bindings": {"type": "boolean"},
                },
            },
        },
        {
            "name": "notion_bind_resources",
            "description": "Bind one or more existing Notion pages or data sources to the current project using the saved access token.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "resource_refs": {"type": "array", "items": resource_ref_schema},
                    "default_alias": {"type": "string"},
                },
                "required": ["resource_refs"],
            },
        },
        {
            "name": "notion_list_bindings",
            "description": "List the Notion resources currently bound to this project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        },
        {
            "name": "notion_get_api_context",
            "description": "Return the current public-integration access token, official API headers, and bound resource IDs so the agent can call the original Notion REST API directly.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        },
    ]


def _handlers() -> dict[str, ToolHandler]:
    return {
        "notion_status": lambda args: status(project_root=args.get("project_root")),
        "notion_setup_guide": lambda args: setup_guide(),
        "notion_auth_browser": lambda args: auth_browser(
            project_root=args.get("project_root"),
            timeout_seconds=int(args.get("timeout_seconds") or 300),
            open_browser=bool(args.get("open_browser", True)),
            page_limit=int(args.get("page_limit") or 500),
        ),
        "notion_start_headless_auth": lambda args: start_headless_auth(
            project_root=args.get("project_root"),
            page_limit=int(args.get("page_limit") or 500),
        ),
        "notion_complete_headless_auth": lambda args: complete_headless_auth(
            project_root=args.get("project_root"),
            handoff_bundle=str(args.get("handoff_bundle") or ""),
        ),
        "notion_refresh_session": lambda args: refresh_session(project_root=args.get("project_root")),
        "notion_clear_project_auth": lambda args: clear_project_auth(
            project_root=args.get("project_root"),
            clear_bindings=bool(args.get("clear_bindings", False)),
        ),
        "notion_bind_resources": lambda args: bind_resources(
            project_root=args.get("project_root"),
            resource_refs=list(args.get("resource_refs") or []),
            default_alias=args.get("default_alias"),
        ),
        "notion_list_bindings": lambda args: list_bindings(project_root=args.get("project_root")),
        "notion_get_api_context": lambda args: get_api_context(project_root=args.get("project_root")),
    }


def _result_text(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _tool_result(payload: dict[str, Any] | str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": _result_text(payload),
            }
        ]
    }
    if is_error:
        result["isError"] = True
    return result


def _jsonrpc_success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": result,
    }


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    message_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    if method == "initialize":
        client_version = str(params.get("protocolVersion") or "2024-11-05")
        return _jsonrpc_success(
            message_id,
            {
                "protocolVersion": client_version,
                "capabilities": {
                    "tools": {
                        "listChanged": False,
                    }
                },
                "serverInfo": {
                    "name": "agent-labbook",
                    "version": __version__,
                },
            },
        )

    if method == "ping":
        return _jsonrpc_success(message_id, {})

    if method == "tools/list":
        return _jsonrpc_success(message_id, {"tools": _tool_definitions()})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        handler = _handlers().get(name)
        if handler is None:
            return _jsonrpc_success(
                message_id,
                _tool_result({"error": f"Unknown tool: {name}"}, is_error=True),
            )
        try:
            return _jsonrpc_success(message_id, _tool_result(handler(arguments)))
        except LabbookError as exc:
            return _jsonrpc_success(message_id, _tool_result({"error": str(exc)}, is_error=True))
        except Exception as exc:  # noqa: BLE001
            return _jsonrpc_success(message_id, _tool_result({"error": f"Internal error: {exc}"}, is_error=True))

    if method == "notifications/initialized":
        return None

    return _jsonrpc_error(message_id, -32601, f"Method not found: {method}")


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _jsonrpc_error(None, -32700, "Parse error")
        else:
            response = _handle_request(request)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
