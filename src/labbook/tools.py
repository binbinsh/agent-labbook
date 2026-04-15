"""MCP tool definitions, handler dispatch, and result formatting.

This module owns:

* The catalogue of ``mcp.types.Tool`` objects (input schemas, annotations).
* The mapping from tool names to synchronous handler callables.
* A small set of helpers for building ``CallToolResult`` values.

The ``mcp_server`` module registers the hooks and delegates here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import mcp.types as types

from . import schemas
from .auth_flow import (
    SETUP_GUIDE_RESOURCE_URI,
    clear_project_auth,
    configure_internal_integration,
    get_api_context,
    prepare_internal_integration,
    setup_guide,
    status,
)
from .binding_ops import (
    DEFAULT_SEARCH_PAGE_SIZE,
    bind_resource_urls,
    bind_resources,
    discover_children,
    list_bindings,
    search_resources,
)
from .binding_ui import start_binding_browser
from .state import TOKEN_ENV_VAR

logger = logging.getLogger("labbook.tools")

__all__ = [
    "tool_definitions",
    "get_handler",
    "tool_result",
    "result_text",
    "structured_payload",
]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ToolResult = dict[str, Any] | tuple[list[types.TextContent], dict[str, Any]]
ToolHandler = Callable[[dict[str, Any]], ToolResult]


# ---------------------------------------------------------------------------
# Annotation / Tool factory helpers
# ---------------------------------------------------------------------------


def _annotations(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool = False,
) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _tool(
    *,
    name: str,
    title: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool = False,
) -> types.Tool:
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required
    return types.Tool(
        name=name,
        title=title,
        description=description,
        inputSchema=input_schema,
        outputSchema=output_schema,
        annotations=_annotations(
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            open_world=open_world,
        ),
    )


# ---------------------------------------------------------------------------
# Shared property fragments
# ---------------------------------------------------------------------------

_PROJECT_ROOT_PROP = {
    "type": "string",
    "description": "Absolute path to the project root directory. Omit to use the current working directory.",
}


# ---------------------------------------------------------------------------
# Tool definitions — core
# ---------------------------------------------------------------------------


def _core_tool_definitions() -> list[types.Tool]:
    return [
        _tool(
            name="notion_status",
            title="Notion Project Status",
            description=(
                "Read the current Internal Integration auth, storage backend, "
                "and bindings status for this project."
            ),
            properties={"project_root": _PROJECT_ROOT_PROP},
            output_schema=schemas.status_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            description="Return the setup guide for the Internal Integration workflow.",
            properties={},
            output_schema=schemas.setup_guide_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_prepare_internal_integration",
            title="Prepare Internal Integration Setup",
            description=(
                "Open the Notion integrations dashboard and detect available "
                "local storage backends before collecting the Internal Integration Secret."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "open_browser": {
                    "type": "boolean",
                    "description": (
                        "Whether to automatically open the Notion integrations "
                        "dashboard in the system browser. Defaults to true."
                    ),
                },
            },
            output_schema=schemas.prepare_output(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
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
                    "description": (
                        "Where to store the secret. 'auto' prefers keychain, "
                        "falls back to 1Password. Defaults to 'auto'."
                    ),
                },
                "op_vault": {
                    "type": "string",
                    "description": "1Password vault name. Only used when storage is '1password'.",
                },
                "op_item_title": {
                    "type": "string",
                    "description": "Custom title for the 1Password item. Only used when storage is '1password'.",
                },
            },
            required=["secret"],
            output_schema=schemas.configure_output(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Tool definitions — binding
# ---------------------------------------------------------------------------


def _binding_tool_definitions() -> list[types.Tool]:
    resource_ref_schema = schemas.binding_resource_ref()
    return [
        _tool(
            name="notion_search_resources",
            title="Search Notion Resources",
            description=(
                "Search the pages and data sources that the Internal Integration bot can access. "
                f"Defaults to {DEFAULT_SEARCH_PAGE_SIZE} results."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "query": {
                    "type": "string",
                    "description": "Search query string to filter Notion pages and data sources by title.",
                },
                "page_size": {
                    "type": "integer",
                    "description": f"Maximum number of results to return. Defaults to {DEFAULT_SEARCH_PAGE_SIZE}.",
                },
            },
            output_schema=schemas.search_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        ),
        _tool(
            name="notion_discover_children",
            title="Discover Immediate Children",
            description=(
                "Inspect the immediate child pages or entries beneath a specific page or data source. "
                "Useful for tree-based binding UIs and MCP-driven selection flows."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_id_or_url": {
                    "type": "string",
                    "description": "Notion resource UUID or full URL of the parent resource to explore.",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["page", "data_source", "database"],
                    "description": "Type of the parent resource. Helps resolve ambiguity when only a UUID is provided.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of child resources to return.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["shallow", "deep"],
                    "description": "Discovery mode: 'shallow' returns only immediate children, 'deep' recurses into subtrees.",
                },
            },
            required=["resource_id_or_url"],
            output_schema=schemas.discovery_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        ),
        _tool(
            name="notion_bind_resource_urls",
            title="Bind Resource URLs",
            description=(
                "Bind one or more Notion page or data source URLs directly. "
                "This is the fastest path when the user already knows the exact Notion links."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_urls": {
                    **schemas.array(schemas.string()),
                    "description": "List of Notion page or data source URLs to bind to this project.",
                },
                "selection_scope": {
                    "type": "string",
                    "enum": ["resource", "subtree"],
                    "description": (
                        "Whether to bind only the specified resources or include their "
                        "entire subtrees. Defaults to 'subtree'."
                    ),
                },
                "default_alias": {
                    "type": "string",
                    "description": "Alias to assign to the first bound resource if only one URL is provided.",
                },
            },
            required=["resource_urls"],
            output_schema=schemas.bindings_output(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_bind_resources",
            title="Bind Resources",
            description=(
                "Bind one or more existing Notion pages or data sources to the current project using the "
                "configured Internal Integration secret. Pass selection_scope='subtree' to mark a root "
                "resource and treat nested content as included."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "resource_refs": {
                    "type": "array",
                    "items": resource_ref_schema,
                    "description": "Array of resource reference objects describing the Notion resources to bind.",
                },
                "default_alias": {
                    "type": "string",
                    "description": "Alias to assign to the first bound resource if only one ref is provided.",
                },
            },
            required=["resource_refs"],
            output_schema=schemas.bindings_output(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_open_binding_browser",
            title="Open Binding Browser",
            description=(
                "Start a local browser-based chooser for selecting Notion roots. "
                "Use this on desktop machines; in headless environments use MCP search, discovery, and URL binding instead."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "open_browser": {
                    "type": "boolean",
                    "description": "Whether to automatically open the system browser. Defaults to true.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "How long to keep the local chooser server running, in seconds. Defaults to 1800.",
                },
                "page_size": {
                    "type": "integer",
                    "description": (
                        f"Number of Notion resources to show per page in the chooser. "
                        f"Defaults to {DEFAULT_SEARCH_PAGE_SIZE}."
                    ),
                },
            },
            output_schema=schemas.binding_browser_output(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Tool definitions — tail
# ---------------------------------------------------------------------------


def _tail_tool_definitions() -> list[types.Tool]:
    return [
        _tool(
            name="notion_list_bindings",
            title="List Bindings",
            description="List the Notion resources currently bound to this project.",
            properties={"project_root": _PROJECT_ROOT_PROP},
            output_schema=schemas.bindings_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_get_api_context",
            title="Get API Context",
            description=(
                "Return the Internal Integration secret, official Notion API headers, "
                "and bound resource IDs for direct API calls."
            ),
            properties={"project_root": _PROJECT_ROOT_PROP},
            output_schema=schemas.api_context_output(),
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        ),
        _tool(
            name="notion_clear_project_auth",
            title="Clear Project Auth",
            description=(
                "Remove the saved project-local session and delete the stored keychain or "
                "1Password secret. Optionally clear bound resources too."
            ),
            properties={
                "project_root": _PROJECT_ROOT_PROP,
                "clear_bindings": {
                    "type": "boolean",
                    "description": (
                        "Whether to also remove all bound Notion resources for this project. "
                        "Defaults to false."
                    ),
                },
            },
            output_schema=schemas.clear_project_auth_output(),
            read_only=False,
            destructive=True,
            idempotent=True,
            open_world=True,
        ),
    ]


def tool_definitions() -> list[types.Tool]:
    """Return the full ordered list of MCP tool definitions."""
    return [
        *_core_tool_definitions(),
        *_binding_tool_definitions(),
        *_tail_tool_definitions(),
    ]


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def _setup_guide_payload() -> ToolResult:
    guide = setup_guide()
    return (
        [types.TextContent(type="text", text=guide)],
        {
            "guide_markdown": guide,
            "resource_uri": SETUP_GUIDE_RESOURCE_URI,
        },
    )


def _build_handlers() -> dict[str, ToolHandler]:
    return {
        "notion_status": lambda args: status(
            project_root=args.get("project_root"),
        ),
        "notion_setup_guide": lambda _args: _setup_guide_payload(),
        "notion_prepare_internal_integration": lambda args: (
            prepare_internal_integration(
                project_root=args.get("project_root"),
                open_browser=bool(args.get("open_browser", True)),
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
            open_browser=bool(args.get("open_browser", True)),
            timeout_seconds=int(args.get("timeout_seconds") or 1800),
            page_size=int(args.get("page_size") or DEFAULT_SEARCH_PAGE_SIZE),
        ).payload(),
        "notion_list_bindings": lambda args: list_bindings(
            project_root=args.get("project_root"),
        ),
        "notion_get_api_context": lambda args: get_api_context(
            project_root=args.get("project_root"),
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
    """Look up the synchronous handler for a given tool *name*."""
    return _handlers().get(name)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


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
