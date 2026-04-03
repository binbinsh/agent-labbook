from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import __version__
from .service import (
    DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
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

SERVER_NAME = "agent-labbook"
server = Server(SERVER_NAME)


def _tool_definitions() -> list[types.Tool]:
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
        types.Tool(
            name="notion_status",
            description="Check whether this project already has a saved Notion public-integration session and which Notion resources are bound locally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="notion_setup_guide",
            description="Return the setup guide for the hosted public integration and its Cloudflare Worker backend.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="notion_auth_browser",
            description=(
                "Open the official Notion public-integration consent flow in a browser and wait for the selected "
                f"bindings to be saved into this project. Browser auth can take several minutes; the default wait is "
                f"{DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS} seconds, and the default page_limit is "
                f"{DEFAULT_BROWSER_AUTH_PAGE_LIMIT}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                    "open_browser": {"type": "boolean"},
                    "page_limit": {"type": "integer"},
                },
            },
        ),
        types.Tool(
            name="notion_start_headless_auth",
            description="Create a headless public-integration auth URL. The user can finish auth in any browser and then paste the returned handoff bundle back into Codex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "page_limit": {"type": "integer"},
                },
            },
        ),
        types.Tool(
            name="notion_complete_headless_auth",
            description="Finish a headless public-integration auth flow using the handoff bundle shown by the Worker callback page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "handoff_bundle": {"type": "string"},
                },
                "required": ["handoff_bundle"],
            },
        ),
        types.Tool(
            name="notion_refresh_session",
            description="Refresh the saved Notion public-integration session for this project through the Worker backend.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="notion_clear_project_auth",
            description="Remove the saved project-local Notion session, and optionally the bound resources too.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "clear_bindings": {"type": "boolean"},
                },
            },
        ),
        types.Tool(
            name="notion_bind_resources",
            description="Bind one or more existing Notion pages or data sources to the current project using the saved access token.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                    "resource_refs": {"type": "array", "items": resource_ref_schema},
                    "default_alias": {"type": "string"},
                },
                "required": ["resource_refs"],
            },
        ),
        types.Tool(
            name="notion_list_bindings",
            description="List the Notion resources currently bound to this project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        ),
        types.Tool(
            name="notion_get_api_context",
            description="Return the current public-integration access token, official API headers, and bound resource IDs so the agent can call the original Notion REST API directly.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
            },
        ),
    ]


def _handlers() -> dict[str, ToolHandler]:
    return {
        "notion_status": lambda args: status(project_root=args.get("project_root")),
        "notion_setup_guide": lambda args: setup_guide(),
        "notion_auth_browser": lambda args: auth_browser(
            project_root=args.get("project_root"),
            timeout_seconds=args.get("timeout_seconds"),
            open_browser=bool(args.get("open_browser", True)),
            page_limit=args.get("page_limit"),
        ),
        "notion_start_headless_auth": lambda args: start_headless_auth(
            project_root=args.get("project_root"),
            page_limit=args.get("page_limit"),
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


def _structured_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {"result": payload}


def _tool_result(payload: dict[str, Any] | str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_result_text(payload))],
        structuredContent=_structured_payload(payload),
        isError=is_error,
    )


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return _tool_definitions()


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
    handler = _handlers().get(name)
    if handler is None:
        return _tool_result({"error": f"Unknown tool: {name}"}, is_error=True)

    try:
        payload = handler(arguments or {})
    except LabbookError as exc:
        return _tool_result({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        return _tool_result({"error": f"Internal error: {exc}"}, is_error=True)

    return _tool_result(payload)


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
