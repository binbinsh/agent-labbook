"""MCP server: tool definitions, resources, prompts, and entry points."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
from urllib import parse

import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from . import __version__
from .state import TOKEN_ENV_VAR, LabbookError

logger = logging.getLogger("labbook.server")

__all__ = ["SERVER_NAME", "server", "run", "main"]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolResult = dict[str, Any] | tuple[list[types.TextContent], dict[str, Any]]
ToolHandler = Callable[[dict[str, Any]], ToolResult]

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
# Resource URIs (imported lazily from auth to avoid circular imports)
# ---------------------------------------------------------------------------

SETUP_GUIDE_RESOURCE_URI = "labbook://agent-labbook/setup-guide"
STATUS_RESOURCE_URI = "labbook://agent-labbook/project/status"
BINDINGS_RESOURCE_URI = "labbook://agent-labbook/project/bindings"
STATUS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/status{?project_root}"
BINDINGS_RESOURCE_TEMPLATE = "labbook://agent-labbook/project/bindings{?project_root}"

# ---------------------------------------------------------------------------
# Tool / result helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT_PROP: dict[str, Any] = {
    "type": "string",
    "description": "Absolute path to the project root directory. Omit to use the current working directory.",
}


def _ann(
    *, ro: bool, dest: bool, idem: bool, ow: bool = False
) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=ro,
        destructiveHint=dest,
        idempotentHint=idem,
        openWorldHint=ow,
    )


def _tool(
    *,
    name: str,
    title: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    ro: bool,
    dest: bool,
    idem: bool,
    ow: bool = False,
) -> types.Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return types.Tool(
        name=name,
        title=title,
        description=description,
        inputSchema=schema,
        outputSchema=None,
        annotations=_ann(ro=ro, dest=dest, idem=idem, ow=ow),
    )


def result_text(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def structured_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {"result": payload}


def tool_result(
    payload: dict[str, Any] | str,
    *,
    is_error: bool = False,
) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=result_text(payload))],
        structuredContent=None if is_error else structured_payload(payload),
        isError=is_error,
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

DEFAULT_SEARCH_PAGE_SIZE = 25


def _tool_definitions() -> list[types.Tool]:
    return [
        # --- core ---
        _tool(
            name="notion_status",
            title="Notion Project Status",
            description="Read the current Internal Integration auth, storage backend, and bindings status for this project.",
            properties={"project_root": _PROJECT_ROOT_PROP},
            ro=True,
            dest=False,
            idem=True,
        ),
        _tool(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            description="Return the setup guide for the Internal Integration workflow.",
            properties={},
            ro=True,
            dest=False,
            idem=True,
        ),
        _tool(
            name="notion_prepare_internal_integration",
            title="Prepare Internal Integration Setup",
            description="Open the Notion integrations dashboard and detect available local storage backends before collecting the Internal Integration Secret.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "open_browser": {
                    "type": "boolean",
                    "description": "Whether to automatically open the Notion integrations dashboard in the system browser.",
                },
            },
            ro=False,
            dest=False,
            idem=False,
            ow=True,
        ),
        _tool(
            name="notion_configure_internal_integration",
            title="Configure Internal Integration",
            description=(
                "Validate and store a Notion Internal Integration secret for this project. "
                "storage='auto' prefers the local system keychain and falls back to 1Password "
                "when keychain is unavailable. Pass storage='1password' only when you explicitly "
                f"want 1Password. Use {TOKEN_ENV_VAR} instead when you prefer an environment-only override."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "secret": {
                    "type": "string",
                    "description": "The Notion Internal Integration Secret (starts with 'secret_' or 'ntn_').",
                },
                "storage": {
                    "type": "string",
                    "enum": ["auto", "keychain", "1password"],
                    "description": "Where to store the secret. Defaults to 'auto'.",
                },
                "op_vault": {
                    "type": "string",
                    "description": "1Password vault name. Only used when storage is '1password'.",
                },
                "op_item_title": {
                    "type": "string",
                    "description": "Custom title for the 1Password item.",
                },
            },
            required=["secret"],
            ro=False,
            dest=False,
            idem=False,
            ow=True,
        ),
        # --- binding ---
        _tool(
            name="notion_search_resources",
            title="Search Notion Resources",
            description=f"Search the pages and data sources that the Internal Integration bot can access. Defaults to {DEFAULT_SEARCH_PAGE_SIZE} results.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "query": {"type": "string", "description": "Search query string."},
                "page_size": {
                    "type": "integer",
                    "description": f"Maximum results. Defaults to {DEFAULT_SEARCH_PAGE_SIZE}.",
                },
            },
            ro=True,
            dest=False,
            idem=True,
            ow=True,
        ),
        _tool(
            name="notion_discover_children",
            title="Discover Immediate Children",
            description="Inspect the immediate child pages or entries beneath a specific page or data source.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_id_or_url": {
                    "type": "string",
                    "description": "Notion resource UUID or full URL of the parent resource.",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["page", "data_source", "database"],
                    "description": "Type of the parent resource.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum child resources to return.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["shallow", "deep"],
                    "description": "Discovery mode.",
                },
            },
            required=["resource_id_or_url"],
            ro=True,
            dest=False,
            idem=True,
            ow=True,
        ),
        _tool(
            name="notion_bind_resource_urls",
            title="Bind Resource URLs",
            description="Bind one or more Notion page or data source URLs directly.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of Notion URLs to bind.",
                },
                "selection_scope": {
                    "type": "string",
                    "enum": ["resource", "subtree"],
                    "description": "Bind scope. Defaults to 'subtree'.",
                },
                "default_alias": {
                    "type": "string",
                    "description": "Alias for the first resource if only one URL.",
                },
            },
            required=["resource_urls"],
            ro=False,
            dest=False,
            idem=False,
            ow=True,
        ),
        _tool(
            name="notion_bind_resources",
            title="Bind Resources",
            description="Bind one or more existing Notion pages or data sources using resource reference objects.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource_id_or_url": {"type": "string"},
                            "resource_type": {
                                "type": "string",
                                "enum": ["page", "data_source", "database"],
                            },
                            "resource_url": {"type": "string"},
                            "alias": {"type": "string"},
                            "title": {"type": "string"},
                            "selection_scope": {
                                "type": "string",
                                "enum": ["resource", "subtree"],
                            },
                        },
                        "required": ["resource_id_or_url"],
                        "additionalProperties": False,
                    },
                    "description": "Array of resource reference objects.",
                },
                "default_alias": {
                    "type": "string",
                    "description": "Alias for the first resource if only one ref.",
                },
            },
            required=["resource_refs"],
            ro=False,
            dest=False,
            idem=False,
            ow=True,
        ),
        _tool(
            name="notion_open_binding_browser",
            title="Open Binding Browser",
            description="Start a browser-based chooser for selecting Notion roots.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "open_browser": {
                    "type": "boolean",
                    "description": "Whether to automatically open the system browser.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Chooser server timeout in seconds. Defaults to 1800.",
                },
                "page_size": {
                    "type": "integer",
                    "description": f"Resources per page. Defaults to {DEFAULT_SEARCH_PAGE_SIZE}.",
                },
                "host": {
                    "type": "string",
                    "description": "Network interface. Defaults to 127.0.0.1.",
                },
                "port": {
                    "type": "integer",
                    "description": "TCP port. Defaults to 0 (ephemeral).",
                },
                "public_base_url": {
                    "type": "string",
                    "description": "External base URL for reverse proxy access.",
                },
                "allowed_origins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional trusted browser origins.",
                },
            },
            ro=False,
            dest=False,
            idem=False,
            ow=True,
        ),
        # --- tail ---
        _tool(
            name="notion_list_bindings",
            title="List Bindings",
            description="List the Notion resources currently bound to this project.",
            properties={"project_root": _PROJECT_ROOT_PROP},
            ro=True,
            dest=False,
            idem=True,
        ),
        _tool(
            name="notion_get_api_context",
            title="Get API Context",
            description="Return the Internal Integration secret, official Notion API headers, and bound resource IDs for direct API calls.",
            properties={"project_root": _PROJECT_ROOT_PROP},
            ro=True,
            dest=False,
            idem=True,
            ow=True,
        ),
        _tool(
            name="notion_clear_project_auth",
            title="Clear Project Auth",
            description="Remove the saved project-local session and delete the stored keychain or 1Password secret.",
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "clear_bindings": {
                    "type": "boolean",
                    "description": "Also remove all bound resources. Defaults to false.",
                },
            },
            ro=False,
            dest=True,
            idem=True,
            ow=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def _build_handlers() -> dict[str, ToolHandler]:
    from .auth import (
        clear_project_auth,
        configure_internal_integration,
        get_api_context,
        prepare_internal_integration,
        setup_guide,
        status,
    )
    from .browser_ui import start_binding_browser
    from .notion import (
        bind_resource_urls,
        bind_resources,
        discover_children,
        list_bindings,
        search_resources,
    )

    def _setup_guide_payload(_args: dict[str, Any]) -> ToolResult:
        guide = setup_guide()
        return (
            [types.TextContent(type="text", text=guide)],
            {"guide_markdown": guide, "resource_uri": SETUP_GUIDE_RESOURCE_URI},
        )

    return {
        "notion_status": lambda args: status(project_root=args.get("project_root")),
        "notion_setup_guide": _setup_guide_payload,
        "notion_prepare_internal_integration": lambda args: (
            prepare_internal_integration(
                project_root=args.get("project_root"),
                open_browser=args.get("open_browser"),
            )
        ),
        "notion_configure_internal_integration": lambda args: (
            configure_internal_integration(
                project_root=args.get("project_root"),
                secret=str(args.get("secret") or ""),
                storage=args.get("storage"),
                op_vault=args.get("op_vault"),
                op_item_title=args.get("op_item_title"),
            )
        ),
        "notion_search_resources": lambda args: search_resources(
            project_root=args.get("project_root"),
            query=args.get("query"),
            page_size=args.get("page_size"),
        ),
        "notion_discover_children": lambda args: discover_children(
            project_root=args.get("project_root"),
            resource_id_or_url=str(args.get("resource_id_or_url") or ""),
            resource_type=args.get("resource_type"),
            limit=args.get("limit"),
            mode=args.get("mode"),
        ),
        "notion_bind_resource_urls": lambda args: bind_resource_urls(
            project_root=args.get("project_root"),
            resource_urls=list(args.get("resource_urls") or []),
            selection_scope=args.get("selection_scope") or "subtree",
            default_alias=args.get("default_alias"),
        ),
        "notion_bind_resources": lambda args: bind_resources(
            project_root=args.get("project_root"),
            resource_refs=list(args.get("resource_refs") or []),
            default_alias=args.get("default_alias"),
        ),
        "notion_open_binding_browser": lambda args: start_binding_browser(
            project_root=args.get("project_root"),
            open_browser=args.get("open_browser"),
            timeout_seconds=int(args.get("timeout_seconds") or 1800),
            page_size=int(args.get("page_size") or DEFAULT_SEARCH_PAGE_SIZE),
            host=str(args.get("host") or "127.0.0.1"),
            port=int(args.get("port") or 0),
            public_base_url=args.get("public_base_url"),
            allowed_origins=list(args.get("allowed_origins") or []),
        ).payload(),
        "notion_list_bindings": lambda args: list_bindings(
            project_root=args.get("project_root")
        ),
        "notion_get_api_context": lambda args: get_api_context(
            project_root=args.get("project_root")
        ),
        "notion_clear_project_auth": lambda args: clear_project_auth(
            project_root=args.get("project_root"),
            clear_bindings=bool(args.get("clear_bindings", False)),
        ),
    }


_HANDLERS: dict[str, ToolHandler] | None = None


def _handlers() -> dict[str, ToolHandler]:
    global _HANDLERS  # noqa: PLW0603
    if _HANDLERS is None:
        _HANDLERS = _build_handlers()
    return _HANDLERS


def get_handler(name: str) -> ToolHandler | None:
    return _handlers().get(name)


# ---------------------------------------------------------------------------
# MCP hooks — Tools
# ---------------------------------------------------------------------------


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return _tool_definitions()


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
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
            content=content_items, structuredContent=structured, isError=False
        )
    return tool_result(result)


# ---------------------------------------------------------------------------
# MCP hooks — Resources
# ---------------------------------------------------------------------------


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            uri=SETUP_GUIDE_RESOURCE_URI,
            description="Static setup guidance for using a Notion Internal Integration secret with Notion Agent Labbook.",
            mimeType="text/markdown",
        ),
        types.Resource(
            name="notion_project_status",
            title="Notion Project Status",
            uri=STATUS_RESOURCE_URI,
            description="Read-only JSON snapshot of the current project's Internal Integration auth, storage backend, and bindings state.",
            mimeType="application/json",
        ),
        types.Resource(
            name="notion_project_bindings",
            title="Notion Project Bindings",
            uri=BINDINGS_RESOURCE_URI,
            description="Read-only JSON snapshot of the current project's bound Notion resources.",
            mimeType="application/json",
        ),
    ]


@server.list_resource_templates()
async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            name="notion_project_status_by_root",
            title="Notion Project Status By Root",
            uriTemplate=STATUS_RESOURCE_TEMPLATE,
            description="Read-only JSON project status for an explicit project_root query parameter.",
            mimeType="application/json",
        ),
        types.ResourceTemplate(
            name="notion_project_bindings_by_root",
            title="Notion Project Bindings By Root",
            uriTemplate=BINDINGS_RESOURCE_TEMPLATE,
            description="Read-only JSON bindings for an explicit project_root query parameter.",
            mimeType="application/json",
        ),
    ]


def _resource_project_root(uri: str) -> str | None:
    parsed = parse.urlsplit(uri)
    values = parse.parse_qs(parsed.query, keep_blank_values=False)
    project_root = str((values.get("project_root") or [""])[0]).strip()
    return project_root or None


@server.read_resource()
async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
    from .auth import project_status_resource, setup_guide
    from .notion import project_bindings_resource_text

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
# MCP hooks — Prompts
# ---------------------------------------------------------------------------


def _prompt_project_suffix(arguments: dict[str, str] | None) -> str:
    project_root = str((arguments or {}).get("project_root") or "").strip()
    if not project_root:
        return "Use the current working directory as the project root."
    return f"Use {project_root} as the project root."


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="notion_connect_project",
            title="Connect Project To Notion",
            description="Recommended workflow for connecting the current project to Notion with an Internal Integration secret.",
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description="Optional absolute project path.",
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="notion_use_bound_resources",
            title="Use Bound Notion Resources",
            description="Recommended workflow for checking bindings and then calling the official Notion API.",
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description="Optional absolute project path.",
                    required=False,
                )
            ],
        ),
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    suffix = _prompt_project_suffix(arguments)
    if name == "notion_connect_project":
        text = "\n".join(
            [
                "Connect this project to Notion with an Internal Integration secret.",
                suffix,
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
                suffix,
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
                role="user", content=types.TextContent(type="text", text=text)
            )
        ],
    )


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
