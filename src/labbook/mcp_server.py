from __future__ import annotations

import asyncio
import json
from typing import Any, Callable
from urllib import parse

import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import __version__
from .auth_flow import (
    BINDINGS_RESOURCE_TEMPLATE,
    BINDINGS_RESOURCE_URI,
    SETUP_GUIDE_RESOURCE_URI,
    STATUS_RESOURCE_TEMPLATE,
    STATUS_RESOURCE_URI,
    clear_project_auth,
    configure_internal_integration,
    get_api_context,
    prepare_internal_integration,
    project_status_resource,
    setup_guide,
    status,
)
from .binding_ops import (
    DEFAULT_SEARCH_PAGE_SIZE,
    bind_resource_urls,
    bind_resources,
    discover_children,
    list_bindings,
    project_bindings_resource_text,
    search_resources,
)
from .binding_ui import start_binding_browser
from .state import LabbookError, TOKEN_ENV_VAR


StructuredToolResult = dict[str, Any]
ToolSuccessResult = (
    StructuredToolResult | tuple[list[types.TextContent], StructuredToolResult]
)
ToolHandler = Callable[[dict[str, Any]], ToolSuccessResult]

SERVER_NAME = "agent-labbook"
SERVER_INSTRUCTIONS = (
    "Notion Agent Labbook exposes read-only project context through MCP resources and mutating "
    "steps through tools. This version uses a Notion Internal Integration secret directly. Prefer "
    "the status and bindings resources before calling tools. Detect available storage backends and "
    "ask the user to choose between system keychain and 1Password when both are available. Do not "
    "echo the integration secret back to the user."
)
server = Server(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)


def _tool_annotations(
    *,
    title: str,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool = False,
) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=title,
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
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return types.Tool(
        name=name,
        title=title,
        description=description,
        inputSchema=schema,
        outputSchema=output_schema,
        annotations=_tool_annotations(
            title=title,
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            open_world=open_world,
        ),
    )


def _string_schema(*, enum: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if enum:
        schema["enum"] = enum
    return schema


def _integer_schema() -> dict[str, Any]:
    return {"type": "integer"}


def _boolean_schema() -> dict[str, Any]:
    return {"type": "boolean"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _array_schema(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional_properties: bool | dict[str, Any] = True,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional_properties,
    }
    if required:
        schema["required"] = required
    return schema


def _binding_resource_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "alias": _string_schema(),
            "resource_id": _string_schema(),
            "resource_type": _string_schema(enum=["page", "data_source"]),
            "resource_url": _nullable(_string_schema()),
            "title": _string_schema(),
            "source": _string_schema(),
            "bound_at": _string_schema(),
            "selection_scope": _string_schema(enum=["resource", "subtree"]),
        },
        required=["alias", "resource_id", "resource_type", "selection_scope"],
    )


def _storage_backend_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "backend": _string_schema(enum=["keychain", "1password"]),
            "display_name": _string_schema(),
            "available": _boolean_schema(),
            "selected_by_default": _boolean_schema(),
            "reason": _nullable(_string_schema()),
            "details": _object_schema({}, additional_properties=True),
        },
        required=[
            "backend",
            "display_name",
            "available",
            "selected_by_default",
            "details",
        ],
    )


def _secret_plan_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "mode": _string_schema(enum=["env", "keychain", "1password"]),
            "reason": _string_schema(),
        },
        required=["mode", "reason"],
    )


def _binding_option_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "mode": _string_schema(
                enum=["wait_for_auth", "url", "local_browser", "manual_mcp"]
            ),
            "label": _string_schema(),
            "available": _boolean_schema(),
            "recommended": _boolean_schema(),
            "reason": _string_schema(),
        },
        required=["mode", "label", "available", "recommended", "reason"],
    )


def _binding_recommendation_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "mode": _string_schema(
                enum=["wait_for_auth", "url", "local_browser", "manual_mcp"]
            ),
            "reason": _string_schema(),
        },
        required=["mode", "reason"],
    )


def _status_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "integration": _string_schema(),
            "project_root": _string_schema(),
            "authentication_mode": _string_schema(enum=["internal_integration"]),
            "authenticated": _boolean_schema(),
            "token_source": _nullable(
                _string_schema(enum=["env", "keychain", "1password"])
            ),
            "storage": _nullable(_string_schema(enum=["keychain", "1password"])),
            "env_token_present": _boolean_schema(),
            "keyring_backend": _string_schema(),
            "keyring_error": _nullable(_string_schema()),
            "onepassword_error": _nullable(_string_schema()),
            "storage_error": _nullable(_string_schema()),
            "storage_options": _array_schema(_storage_backend_schema()),
            "storage_default": _nullable(
                _string_schema(enum=["keychain", "1password"])
            ),
            "secret_plan": _secret_plan_schema(),
            "storage_choice_required": _boolean_schema(),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "bot_owner_type": _nullable(_string_schema()),
            "configured_at": _nullable(_string_schema()),
            "session_path": _string_schema(),
            "session_exists": _boolean_schema(),
            "bindings_path": _string_schema(),
            "bindings_configured": _boolean_schema(),
            "bindings_count": _integer_schema(),
            "recommended_action": _string_schema(),
            "authentication_hint": _string_schema(),
            "storage_hint": _string_schema(),
            "binding_hint": _string_schema(),
            "likely_headless": _boolean_schema(),
            "binding_recommendation": _binding_recommendation_schema(),
            "binding_options": _array_schema(_binding_option_schema()),
            "setup_resource_uri": _string_schema(),
            "available_env_var": _string_schema(),
            "notion_integrations_url": _string_schema(),
            "notion_docs_url": _string_schema(),
            "notion_version": _string_schema(),
        },
        required=[
            "integration",
            "project_root",
            "authentication_mode",
            "authenticated",
            "env_token_present",
            "keyring_backend",
            "storage_options",
            "secret_plan",
            "storage_choice_required",
            "session_path",
            "session_exists",
            "bindings_path",
            "bindings_configured",
            "bindings_count",
            "recommended_action",
            "authentication_hint",
            "storage_hint",
            "binding_hint",
            "likely_headless",
            "binding_recommendation",
            "binding_options",
            "setup_resource_uri",
            "available_env_var",
            "notion_integrations_url",
            "notion_docs_url",
            "notion_version",
        ],
    )


def _setup_guide_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "guide_markdown": _string_schema(),
            "resource_uri": _string_schema(),
        },
        required=["guide_markdown", "resource_uri"],
    )


def _prepare_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "notion_integrations_url": _string_schema(),
            "notion_docs_url": _string_schema(),
            "browser_opened": _boolean_schema(),
            "open_browser_attempted": _boolean_schema(),
            "storage_options": _array_schema(_storage_backend_schema()),
            "storage_default": _nullable(
                _string_schema(enum=["keychain", "1password"])
            ),
            "storage_choice_required": _boolean_schema(),
            "secret_label": _string_schema(),
            "setup_steps": _array_schema(_string_schema()),
            "recommended_next_action": _string_schema(),
        },
        required=[
            "project_root",
            "notion_integrations_url",
            "notion_docs_url",
            "browser_opened",
            "open_browser_attempted",
            "storage_options",
            "storage_choice_required",
            "secret_label",
            "setup_steps",
            "recommended_next_action",
        ],
    )


def _configure_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "ok": _boolean_schema(),
            "authentication_mode": _string_schema(enum=["internal_integration"]),
            "project_root": _string_schema(),
            "token_source": _string_schema(enum=["keychain", "1password"]),
            "storage": _string_schema(enum=["keychain", "1password"]),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "bot_owner_type": _nullable(_string_schema()),
            "storage_options": _array_schema(_storage_backend_schema()),
            "recommended_next_action": _string_schema(),
            "op_vault": _nullable(_string_schema()),
            "op_item_title": _nullable(_string_schema()),
            "keyring_backend": _string_schema(),
        },
        required=[
            "ok",
            "authentication_mode",
            "project_root",
            "token_source",
            "storage",
            "storage_options",
            "recommended_next_action",
            "keyring_backend",
        ],
    )


def _search_result_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "resource_id": _string_schema(),
            "resource_type": _string_schema(enum=["page", "data_source"]),
            "resource_url": _nullable(_string_schema()),
            "title": _string_schema(),
            "last_edited_time": _nullable(_string_schema()),
            "parent": _nullable(_object_schema({}, additional_properties=True)),
        },
        required=["resource_id", "resource_type", "title"],
    )


def _search_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "query": _nullable(_string_schema()),
            "page_size": _integer_schema(),
            "result_count": _integer_schema(),
            "results": _array_schema(_search_result_schema()),
        },
        required=["project_root", "page_size", "result_count", "results"],
    )


def _discovery_result_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "resource_id": _string_schema(),
            "resource_type": _string_schema(enum=["page", "data_source"]),
            "resource_url": _nullable(_string_schema()),
            "title": _string_schema(),
            "last_edited_time": _nullable(_string_schema()),
            "parent": _nullable(_object_schema({}, additional_properties=True)),
            "parent_type": _nullable(_string_schema()),
            "parent_id": _nullable(_string_schema()),
            "parent_database_id": _nullable(_string_schema()),
            "discovered_parent_id": _nullable(_string_schema()),
            "discovered_root_id": _nullable(_string_schema()),
            "discovered_depth": _nullable(_integer_schema()),
        },
        required=["resource_id", "resource_type", "title"],
    )


def _discovery_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "root_resource": _discovery_result_schema(),
            "page_size": _integer_schema(),
            "mode": _string_schema(enum=["shallow", "deep"]),
            "partial": _boolean_schema(),
            "result_count": _integer_schema(),
            "results": _array_schema(_discovery_result_schema()),
        },
        required=[
            "project_root",
            "root_resource",
            "page_size",
            "mode",
            "partial",
            "result_count",
            "results",
        ],
    )


def _bindings_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "default_resource_alias": _nullable(_string_schema()),
            "resource_count": _integer_schema(),
            "resources": _array_schema(_binding_resource_schema()),
        },
        required=["project_root", "resource_count", "resources"],
    )


def _binding_browser_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "chooser_url": _string_schema(),
            "browser_opened": _boolean_schema(),
            "open_browser_attempted": _boolean_schema(),
            "timeout_seconds": _integer_schema(),
            "page_size": _integer_schema(),
            "workspace_name": _nullable(_string_schema()),
            "recommended_next_action": _string_schema(),
            "headless_flow_hint": _string_schema(),
        },
        required=[
            "project_root",
            "chooser_url",
            "browser_opened",
            "open_browser_attempted",
            "timeout_seconds",
            "page_size",
            "recommended_next_action",
            "headless_flow_hint",
        ],
    )


def _clear_project_auth_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "ok": _boolean_schema(),
            "project_root": _string_schema(),
            "storage": _nullable(_string_schema(enum=["keychain", "1password"])),
            "session_cleared": _boolean_schema(),
            "stored_secret_deleted": _boolean_schema(),
            "bindings_cleared": _boolean_schema(),
            "env_token_still_present": _boolean_schema(),
        },
        required=[
            "ok",
            "project_root",
            "session_cleared",
            "stored_secret_deleted",
            "bindings_cleared",
            "env_token_still_present",
        ],
    )


def _api_context_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "authentication_mode": _string_schema(enum=["internal_integration"]),
            "token_source": _string_schema(enum=["env", "keychain", "1password"]),
            "storage": _nullable(_string_schema(enum=["keychain", "1password"])),
            "access_token": _string_schema(),
            "api_base_url": _string_schema(),
            "notion_version": _string_schema(),
            "headers": _object_schema({}, additional_properties=True),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "bound_resources": _array_schema(_binding_resource_schema()),
            "selection_scope_note": _string_schema(),
        },
        required=[
            "project_root",
            "authentication_mode",
            "token_source",
            "access_token",
            "api_base_url",
            "notion_version",
            "headers",
            "bound_resources",
            "selection_scope_note",
        ],
    )


def _setup_guide_tool_payload() -> ToolSuccessResult:
    guide = setup_guide()
    return (
        [types.TextContent(type="text", text=guide)],
        {
            "guide_markdown": guide,
            "resource_uri": SETUP_GUIDE_RESOURCE_URI,
        },
    )


def _binding_resource_ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "resource_id_or_url": {"type": "string"},
            "resource_id": {"type": "string"},
            "resource_url": {"type": "string"},
            "resource_type": {
                "type": "string",
                "enum": ["page", "data_source", "database"],
            },
            "title": {"type": "string"},
            "alias": {"type": "string"},
            "selection_scope": {"type": "string", "enum": ["resource", "subtree"]},
        },
    }


def _binding_tool_definitions() -> list[types.Tool]:
    resource_ref_schema = _binding_resource_ref_schema()
    return [
        _tool(
            name="notion_search_resources",
            title="Search Notion Resources",
            description=(
                "Search the pages and data sources that the Internal Integration bot can access. "
                f"Defaults to {DEFAULT_SEARCH_PAGE_SIZE} results."
            ),
            properties={
                "project_root": {"type": "string"},
                "query": {"type": "string"},
                "page_size": {"type": "integer"},
            },
            output_schema=_search_output_schema(),
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
                "project_root": {"type": "string"},
                "resource_id_or_url": {"type": "string"},
                "resource_type": {
                    "type": "string",
                    "enum": ["page", "data_source", "database"],
                },
                "limit": {"type": "integer"},
                "mode": {"type": "string", "enum": ["shallow", "deep"]},
            },
            required=["resource_id_or_url"],
            output_schema=_discovery_output_schema(),
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
                "project_root": {"type": "string"},
                "resource_urls": _array_schema(_string_schema()),
                "selection_scope": {
                    "type": "string",
                    "enum": ["resource", "subtree"],
                },
                "default_alias": {"type": "string"},
            },
            required=["resource_urls"],
            output_schema=_bindings_output_schema(),
            read_only=False,
            destructive=True,
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
                "project_root": {"type": "string"},
                "resource_refs": {"type": "array", "items": resource_ref_schema},
                "default_alias": {"type": "string"},
            },
            required=["resource_refs"],
            output_schema=_bindings_output_schema(),
            read_only=False,
            destructive=True,
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
                "project_root": {"type": "string"},
                "open_browser": {"type": "boolean"},
                "timeout_seconds": {"type": "integer"},
                "page_size": {"type": "integer"},
            },
            output_schema=_binding_browser_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
    ]


def _tool_definitions() -> list[types.Tool]:
    return [
        _tool(
            name="notion_status",
            title="Notion Project Status",
            description="Read the current Internal Integration auth, storage backend, and bindings status for this project.",
            properties={"project_root": {"type": "string"}},
            output_schema=_status_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            description="Return the setup guide for the Internal Integration workflow.",
            properties={},
            output_schema=_setup_guide_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_prepare_internal_integration",
            title="Prepare Internal Integration Setup",
            description="Open the Notion integrations dashboard and detect available local storage backends before collecting the Internal Integration Secret.",
            properties={
                "project_root": {"type": "string"},
                "open_browser": {"type": "boolean"},
            },
            output_schema=_prepare_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_configure_internal_integration",
            title="Configure Internal Integration",
            description=(
                "Validate and store a Notion Internal Integration secret for this project. Pass storage='keychain' "
                "to use the local system keychain or storage='1password' to use 1Password. "
                f"Use {TOKEN_ENV_VAR} instead when you prefer an environment-only override."
            ),
            properties={
                "project_root": {"type": "string"},
                "secret": {"type": "string"},
                "storage": {
                    "type": "string",
                    "enum": ["auto", "keychain", "1password"],
                },
                "op_vault": {"type": "string"},
                "op_item_title": {"type": "string"},
            },
            required=["secret"],
            output_schema=_configure_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        *_binding_tool_definitions(),
        _tool(
            name="notion_list_bindings",
            title="List Bindings",
            description="List the Notion resources currently bound to this project.",
            properties={"project_root": {"type": "string"}},
            output_schema=_bindings_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_get_api_context",
            title="Get API Context",
            description="Return the Internal Integration secret, official Notion API headers, and bound resource IDs for direct API calls.",
            properties={"project_root": {"type": "string"}},
            output_schema=_api_context_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        ),
        _tool(
            name="notion_clear_project_auth",
            title="Clear Project Auth",
            description="Remove the saved project-local session and delete the stored keychain or 1Password secret. Optionally clear bound resources too.",
            properties={
                "project_root": {"type": "string"},
                "clear_bindings": {"type": "boolean"},
            },
            output_schema=_clear_project_auth_output_schema(),
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    ]


def _binding_handlers() -> dict[str, ToolHandler]:
    return {
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
    }


def _handlers() -> dict[str, ToolHandler]:
    return {
        "notion_status": lambda args: status(project_root=args.get("project_root")),
        "notion_setup_guide": lambda _args: _setup_guide_tool_payload(),
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
        **_binding_handlers(),
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


def _resource_definitions() -> list[types.Resource]:
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


def _resource_template_definitions() -> list[types.ResourceTemplate]:
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


def _prompt_definitions() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="notion_connect_project",
            title="Connect Project To Notion",
            description="Recommended workflow for connecting the current project to Notion with an Internal Integration secret.",
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description="Optional absolute project path if you are not operating on the current working directory.",
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="notion_use_bound_resources",
            title="Use Bound Notion Resources",
            description="Recommended workflow for checking bindings and then calling the official Notion API with the project's configured Internal Integration secret.",
            arguments=[
                types.PromptArgument(
                    name="project_root",
                    description="Optional absolute project path if you are not operating on the current working directory.",
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
                "2. If the project is not authenticated, call notion_prepare_internal_integration to open the Notion integrations dashboard and detect available storage backends.",
                "3. If notion_status.storage_choice_required is true, ask the user whether to store the secret in system keychain or 1Password.",
                "4. Use notion_configure_internal_integration with the chosen storage value to validate and store the secret.",
                "5. Remind the user to share the target pages or data sources with the integration bot inside Notion.",
                "6. Read notion_status.binding_recommendation and notion_status.binding_options before choosing a binding UX.",
                "7. Ask whether the user can paste exact Notion links. If yes, prefer notion_bind_resource_urls.",
                "8. If not, default to notion_open_binding_browser on desktop-capable environments.",
                "9. In headless environments, use notion_search_resources and notion_discover_children to narrow the tree, then bind the chosen roots with notion_bind_resources.",
                "10. Call notion_get_api_context only when you are ready to use the official Notion API.",
                "11. Never echo the secret back to the user or store it in project files.",
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
                "6. If the project is not authenticated, use notion_status, notion_prepare_internal_integration, and notion_configure_internal_integration first.",
            ]
        )
    else:
        raise LabbookError(f"Unknown prompt: {name}")

    return types.GetPromptResult(
        description=text.splitlines()[0],
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=text),
            )
        ],
    )


def _result_text(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _structured_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {"result": payload}


def _tool_result(
    payload: dict[str, Any] | str, *, is_error: bool = False
) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_result_text(payload))],
        structuredContent=_structured_payload(payload),
        isError=is_error,
    )


def _resource_project_root(uri: str) -> str | None:
    parsed = parse.urlsplit(uri)
    values = parse.parse_qs(parsed.query, keep_blank_values=False)
    project_root = str((values.get("project_root") or [""])[0]).strip()
    return project_root or None


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return _tool_definitions()


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> types.CallToolResult:
    handler = _handlers().get(name)
    if handler is None:
        return _tool_result({"error": f"Unknown tool: {name}"}, is_error=True)

    try:
        result = handler(arguments or {})
    except LabbookError as exc:
        return _tool_result({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        return _tool_result({"error": f"Internal error: {exc}"}, is_error=True)

    if isinstance(result, tuple):
        content_items, structured = result
        return types.CallToolResult(
            content=content_items,
            structuredContent=structured,
            isError=False,
        )
    return _tool_result(result)


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

    if base_uri == SETUP_GUIDE_RESOURCE_URI:
        text = setup_guide()
        mime_type = "text/markdown"
    elif base_uri == STATUS_RESOURCE_URI:
        text = project_status_resource(project_root=project_root)
        mime_type = "application/json"
    elif base_uri == BINDINGS_RESOURCE_URI:
        text = project_bindings_resource_text(project_root=project_root)
        mime_type = "application/json"
    else:
        raise LabbookError(f"Unknown resource: {raw_uri}")

    return [ReadResourceContents(content=text, mime_type=mime_type)]


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return _prompt_definitions()


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    return _prompt_result(name, arguments)


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
