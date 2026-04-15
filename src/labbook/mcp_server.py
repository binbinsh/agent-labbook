"""MCP server — thin orchestration layer.

Creates the ``Server`` instance, registers MCP hooks (tools, resources,
resource templates, prompts), and provides the ``run()`` / ``main()`` entry
points.  All tool definitions, handler dispatch, and JSON schema definitions
live in :mod:`tools` and :mod:`schemas` respectively.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib import parse

import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from . import __version__
from .auth_flow import (
    BINDINGS_RESOURCE_TEMPLATE,
    BINDINGS_RESOURCE_URI,
    SETUP_GUIDE_RESOURCE_URI,
    STATUS_RESOURCE_TEMPLATE,
    STATUS_RESOURCE_URI,
    project_status_resource,
    setup_guide,
)
from .binding_ops import project_bindings_resource_text
from .state import LabbookError
from .tools import get_handler, tool_definitions, tool_result

logger = logging.getLogger("labbook.mcp_server")

__all__ = ["SERVER_NAME", "server", "run", "main"]


# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

SERVER_NAME = "agent-labbook"
SERVER_INSTRUCTIONS = (
    "Notion Agent Labbook connects a local project to Notion through a Notion Internal Integration "
    "secret. It exposes read-only project context through MCP resources (status, bindings, setup guide) "
    "and mutating steps through tools (authenticate, configure secret, search Notion pages and databases, "
    "discover children, bind resources, open binding browser, get API context, clear auth). "
    "Use the status and bindings resources before calling tools. Default to the local system keychain "
    "when it is available, use 1Password only when the user explicitly prefers it or keychain is "
    "unavailable, and treat environment variables as CI or temporary overrides. Do not echo the "
    "integration secret back to the user."
)
server = Server(SERVER_NAME, version=__version__, instructions=SERVER_INSTRUCTIONS)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return tool_definitions()


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> types.CallToolResult:
    handler = get_handler(name)
    if handler is None:
        logger.warning("Unknown tool requested: %s", name)
        return tool_result({"error": f"Unknown tool: {name}"}, is_error=True)

    try:
        logger.debug("Calling tool %s", name)
        result = await asyncio.to_thread(handler, arguments or {})
    except LabbookError as exc:
        logger.warning("Tool %s failed: %s", name, exc)
        return tool_result({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool %s internal error", name)
        return tool_result({"error": f"Internal error: {exc}"}, is_error=True)

    if isinstance(result, tuple):
        content_items, structured = result
        return types.CallToolResult(
            content=content_items,
            structuredContent=structured,
            isError=False,
        )
    return tool_result(result)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _resource_definitions() -> list[types.Resource]:
    return [
        types.Resource(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            uri=SETUP_GUIDE_RESOURCE_URI,
            description=(
                "Static setup guidance for using a Notion Internal Integration "
                "secret with Notion Agent Labbook."
            ),
            mimeType="text/markdown",
        ),
        types.Resource(
            name="notion_project_status",
            title="Notion Project Status",
            uri=STATUS_RESOURCE_URI,
            description=(
                "Read-only JSON snapshot of the current project's Internal Integration "
                "auth, storage backend, and bindings state."
            ),
            mimeType="application/json",
        ),
        types.Resource(
            name="notion_project_bindings",
            title="Notion Project Bindings",
            uri=BINDINGS_RESOURCE_URI,
            description=(
                "Read-only JSON snapshot of the current project's bound Notion resources."
            ),
            mimeType="application/json",
        ),
    ]


def _resource_template_definitions() -> list[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            name="notion_project_status_by_root",
            title="Notion Project Status By Root",
            uriTemplate=STATUS_RESOURCE_TEMPLATE,
            description=(
                "Read-only JSON project status for an explicit project_root query parameter."
            ),
            mimeType="application/json",
        ),
        types.ResourceTemplate(
            name="notion_project_bindings_by_root",
            title="Notion Project Bindings By Root",
            uriTemplate=BINDINGS_RESOURCE_TEMPLATE,
            description=(
                "Read-only JSON bindings for an explicit project_root query parameter."
            ),
            mimeType="application/json",
        ),
    ]


def _resource_project_root(uri: str) -> str | None:
    parsed = parse.urlsplit(uri)
    values = parse.parse_qs(parsed.query, keep_blank_values=False)
    project_root = str((values.get("project_root") or [""])[0]).strip()
    return project_root or None


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    return _resource_definitions()


@server.list_resource_templates()
async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
    return _resource_template_definitions()


@server.read_resource()
async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
    raw_uri = str(uri)
    parsed = parse.urlsplit(raw_uri)
    base_uri = parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    project_root = _resource_project_root(raw_uri)

    try:
        if base_uri == SETUP_GUIDE_RESOURCE_URI:
            text = setup_guide()
            mime_type = "text/markdown"
        elif base_uri == STATUS_RESOURCE_URI:
            text = await asyncio.to_thread(
                project_status_resource, project_root=project_root
            )
            mime_type = "application/json"
        elif base_uri == BINDINGS_RESOURCE_URI:
            text = await asyncio.to_thread(
                project_bindings_resource_text, project_root=project_root
            )
            mime_type = "application/json"
        else:
            raise McpError(
                ErrorData(code=INVALID_PARAMS, message=f"Unknown resource: {raw_uri}")
            )
    except McpError:
        raise
    except LabbookError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    except Exception as exc:
        logger.exception("Resource %s internal error", raw_uri)
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"Internal error: {exc}")
        ) from exc

    return [ReadResourceContents(content=text, mime_type=mime_type)]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def _prompt_definitions() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="notion_connect_project",
            title="Connect Project To Notion",
            description=(
                "Recommended workflow for connecting the current project to Notion "
                "with an Internal Integration secret."
            ),
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description=(
                        "Optional absolute project path if you are not operating "
                        "on the current working directory."
                    ),
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="notion_use_bound_resources",
            title="Use Bound Notion Resources",
            description=(
                "Recommended workflow for checking bindings and then calling the "
                "official Notion API with the project's configured Internal "
                "Integration secret."
            ),
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description=(
                        "Optional absolute project path if you are not operating "
                        "on the current working directory."
                    ),
                    required=False,
                )
            ],
        ),
    ]


def _prompt_project_suffix(arguments: dict[str, str] | None) -> str:
    project_root = str((arguments or {}).get("project_root") or "").strip()
    if not project_root:
        return "Use the current working directory as the project root."
    return f"Use {project_root} as the project root."


def _prompt_result(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    project_suffix = _prompt_project_suffix(arguments)
    if name == "notion_connect_project":
        text = "\n".join(
            [
                "Connect this project to Notion with an Internal Integration secret.",
                project_suffix,
                f"1. Read {STATUS_RESOURCE_URI} or call notion_status.",
                "2. If the project is not authenticated, call notion_prepare_internal_integration.",
                "3. Prefer agent-labbook configure-secret on the same machine. Default to keychain. Use 1Password only when explicitly requested.",
                "4. Remind the user to share the target pages or data sources with the integration bot inside Notion.",
                "5. Prefer notion_bind_resource_urls for exact links, notion_open_binding_browser on desktop, or notion_search_resources plus notion_discover_children in headless environments.",
                "6. Call notion_get_api_context only when you are ready to use the official Notion API.",
                "7. Never echo the secret back to the user or store it in project files.",
            ]
        )
    elif name == "notion_use_bound_resources":
        text = "\n".join(
            [
                "Use the project's bound Notion resources safely.",
                project_suffix,
                f"1. Read {BINDINGS_RESOURCE_URI} or call notion_list_bindings to inspect the current explicit roots and selection_scope values.",
                "2. Call notion_get_api_context only when you are ready to use the official Notion API.",
                "3. If your source content is already markdown, prefer Notion's markdown content APIs over manual block conversion: POST /v1/pages with markdown to create content, GET /v1/pages/{page_id}/markdown to read it back, and PATCH /v1/pages/{page_id}/markdown to update it.",
                "4. See https://developers.notion.com/guides/data-apis/working-with-markdown-content for the markdown API details.",
                "5. Treat the Internal Integration secret like a password and avoid echoing it into logs or chat transcripts.",
                "6. If the project is not authenticated, use notion_status, notion_prepare_internal_integration, and then prefer agent-labbook configure-secret or notion_configure_internal_integration first.",
            ]
        )
    else:
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message=f"Unknown prompt: {name}")
        )

    return types.GetPromptResult(
        description=text.splitlines()[0],
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return _prompt_definitions()


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    try:
        return _prompt_result(name, arguments)
    except McpError:
        raise
    except LabbookError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc
    except Exception as exc:
        logger.exception("Prompt %s internal error", name)
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"Internal error: {exc}")
        ) from exc


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


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
                instructions=SERVER_INSTRUCTIONS,
            ),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
