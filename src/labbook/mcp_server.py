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
from .service import (
    BINDINGS_RESOURCE_TEMPLATE,
    BINDINGS_RESOURCE_URI,
    DEFAULT_BROWSER_AUTH_PAGE_LIMIT,
    DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS,
    SETUP_GUIDE_RESOURCE_URI,
    STATUS_RESOURCE_TEMPLATE,
    STATUS_RESOURCE_URI,
    attach_saved_credential,
    auth_browser,
    bind_resources,
    clear_project_auth,
    complete_headless_auth,
    finalize_pending_auth,
    get_api_context,
    list_bindings,
    list_saved_credentials,
    project_bindings_resource,
    project_status_resource,
    refresh_session,
    selection_browser,
    setup_guide,
    start_headless_auth,
    status,
)
from .state import LabbookError


StructuredToolResult = dict[str, Any]
ToolSuccessResult = StructuredToolResult | tuple[list[types.TextContent], StructuredToolResult]
ToolHandler = Callable[[dict[str, Any]], ToolSuccessResult]

SERVER_NAME = "agent-labbook"
SERVER_INSTRUCTIONS = (
    "Agent Labbook exposes read-only project context through MCP resources and mutating workflow steps through tools. "
    "Prefer the status and bindings resources before calling tools. Use notion_finalize_pending_auth only after "
    "notion_status reports pending_handoff_ready=true. Use notion_get_api_context only when you are ready to call "
    "the official Notion API, and treat the returned access token as sensitive."
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
            "resource_type": _string_schema(),
            "resource_url": _nullable(_string_schema()),
            "title": _string_schema(),
            "source": _string_schema(),
            "bound_at": _string_schema(),
            "selection_scope": _string_schema(enum=["resource", "subtree"]),
        },
        required=["alias", "resource_id", "resource_type", "selection_scope"],
    )


def _saved_credential_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "provider": _string_schema(enum=["keyring", "1password"]),
            "credential_ref": _string_schema(),
            "display_name": _string_schema(),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "authorized_at": _nullable(_string_schema()),
            "updated_at": _nullable(_string_schema()),
            "attached_to_project": _boolean_schema(),
            "metadata": _nullable(_object_schema({}, additional_properties=True)),
        },
        required=["provider", "credential_ref", "display_name", "attached_to_project"],
    )


def _provider_status_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "provider": _string_schema(enum=["keyring", "1password"]),
            "available": _boolean_schema(),
            "selected_by_default": _boolean_schema(),
            "reason": _nullable(_string_schema()),
            "details": _nullable(_object_schema({}, additional_properties=True)),
        },
        required=["provider", "available", "selected_by_default"],
    )


def _credential_provider_diagnostics_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "requested_provider": _string_schema(),
            "resolved_provider": _string_schema(),
            "providers": _array_schema(_provider_status_schema()),
        },
        required=["requested_provider", "resolved_provider", "providers"],
    )


def _pending_auth_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "integration": _string_schema(),
            "mode": _string_schema(enum=["local_browser", "headless"]),
            "session_id": _string_schema(),
            "auth_url": _string_schema(),
            "return_to": _nullable(_string_schema()),
            "timeout_seconds": _nullable(_integer_schema()),
            "page_limit": _nullable(_integer_schema()),
            "started_at": _string_schema(),
        },
        required=["integration", "mode", "session_id", "auth_url", "started_at"],
    )


def _pending_handoff_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "session_id": _nullable(_string_schema()),
            "received_at": _nullable(_string_schema()),
            "return_to": _nullable(_string_schema()),
        }
    )


def _browser_environment_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "preferred_browser_flow": _string_schema(enum=["local_browser", "headless"]),
            "recommended_open_browser": _boolean_schema(),
            "ssh_session_detected": _boolean_schema(),
            "display_detected": _boolean_schema(),
            "graphical_launcher_available": _boolean_schema(),
            "override_source": _nullable(_string_schema()),
            "reason": _string_schema(),
        },
        required=[
            "preferred_browser_flow",
            "recommended_open_browser",
            "ssh_session_detected",
            "display_detected",
            "graphical_launcher_available",
            "reason",
        ],
    )


def _status_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "authenticated": _boolean_schema(),
            "ready": _boolean_schema(),
            "recommended_action": _string_schema(),
            "credential_provider": _nullable(_string_schema()),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "available_saved_credentials_count": _integer_schema(),
            "available_saved_credentials": _array_schema(_saved_credential_schema()),
            "bound_resource_count": _integer_schema(),
            "resources": _array_schema(_binding_resource_schema()),
            "pending_auth": _nullable(_pending_auth_schema()),
            "pending_auth_stale": _boolean_schema(),
            "pending_handoff_ready": _boolean_schema(),
            "pending_handoff": _nullable(_pending_handoff_schema()),
            "pending_handoff_hint": _nullable(_string_schema()),
            "preferred_browser_flow": _string_schema(enum=["local_browser", "headless"]),
            "recommended_open_browser": _boolean_schema(),
            "browser_environment_hint": _string_schema(),
            "browser_environment": _browser_environment_schema(),
            "credential_provider_diagnostics": _nullable(_credential_provider_diagnostics_schema()),
            "credential_provider_diagnostics_error": _nullable(_string_schema()),
            "setup_guide_resource_uri": _string_schema(),
            "status_resource_uri": _string_schema(),
            "bindings_resource_uri": _string_schema(),
        },
        required=[
            "project_root",
            "integration",
            "authenticated",
            "ready",
            "recommended_action",
            "available_saved_credentials_count",
            "available_saved_credentials",
            "bound_resource_count",
            "resources",
            "pending_auth_stale",
            "pending_handoff_ready",
            "preferred_browser_flow",
            "recommended_open_browser",
            "browser_environment_hint",
            "browser_environment",
            "setup_guide_resource_uri",
            "status_resource_uri",
            "bindings_resource_uri",
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


def _auth_browser_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "auth_mode": _string_schema(enum=["local_browser", "headless"]),
            "auth_url": _string_schema(),
            "session_id": _string_schema(),
            "return_to": _nullable(_string_schema()),
            "timeout_seconds": _integer_schema(),
            "page_limit": _integer_schema(),
            "browser_opened": _boolean_schema(),
            "recommended_next_action": _string_schema(),
            "auto_switched_to_headless": _boolean_schema(),
            "reason": _string_schema(),
            "instructions": _string_schema(),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "auth_mode",
            "auth_url",
            "session_id",
            "page_limit",
            "instructions",
        ],
    )


def _start_headless_auth_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "auth_url": _string_schema(),
            "session_id": _string_schema(),
            "page_limit": _integer_schema(),
            "instructions": _string_schema(),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "auth_url",
            "session_id",
            "page_limit",
            "instructions",
        ],
    )


def _selection_browser_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "selection_mode": _string_schema(enum=["local_browser", "headless"]),
            "selection_url": _string_schema(),
            "session_id": _string_schema(),
            "page_limit": _integer_schema(),
            "timeout_seconds": _integer_schema(),
            "return_to": _nullable(_string_schema()),
            "credential_provider": _nullable(_string_schema(enum=["keyring", "1password"])),
            "credential_ref": _nullable(_string_schema()),
            "browser_opened": _boolean_schema(),
            "recommended_next_action": _string_schema(),
            "auto_switched_to_headless": _boolean_schema(),
            "reason": _string_schema(),
            "replaces_existing_bindings": _boolean_schema(),
            "instructions": _string_schema(),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "selection_mode",
            "selection_url",
            "session_id",
            "page_limit",
            "credential_provider",
            "credential_ref",
            "replaces_existing_bindings",
            "instructions",
        ],
    )


def _auth_completion_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "session_path": _string_schema(),
            "binding_path": _string_schema(),
            "default_resource_alias": _nullable(_string_schema()),
            "resources": _array_schema(_binding_resource_schema()),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "session_path",
            "binding_path",
            "resources",
        ],
    )


def _list_saved_credentials_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "credential_provider": _nullable(_string_schema(enum=["keyring", "1password"])),
            "saved_credential_count": _integer_schema(),
            "credentials": _array_schema(_saved_credential_schema()),
        },
        required=[
            "project_root",
            "integration",
            "credential_provider",
            "saved_credential_count",
            "credentials",
        ],
    )


def _attach_saved_credential_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "credential_provider": _string_schema(enum=["keyring", "1password"]),
            "credential_ref": _string_schema(),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "session_path": _string_schema(),
            "binding_path": _string_schema(),
            "cleared_bindings": _boolean_schema(),
            "attached_existing_credential": _boolean_schema(),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "credential_provider",
            "credential_ref",
            "session_path",
            "binding_path",
            "cleared_bindings",
            "attached_existing_credential",
        ],
    )


def _clear_project_auth_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "cleared_session": _boolean_schema(),
            "cleared_pending_auth": _boolean_schema(),
            "cleared_pending_handoff": _boolean_schema(),
            "cleared_local_handoff_server": _boolean_schema(),
            "cleared_bindings": _boolean_schema(),
            "shared_credentials_retained": _boolean_schema(),
        },
        required=[
            "project_root",
            "cleared_session",
            "cleared_pending_auth",
            "cleared_pending_handoff",
            "cleared_local_handoff_server",
            "cleared_bindings",
            "shared_credentials_retained",
        ],
    )


def _bindings_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "binding_path": _string_schema(),
            "default_resource_alias": _nullable(_string_schema()),
            "resources": _array_schema(_binding_resource_schema()),
        },
        required=["project_root", "binding_path", "default_resource_alias", "resources"],
    )


def _refresh_session_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "integration": _string_schema(),
            "backend_url": _string_schema(),
            "oauth_base_url": _string_schema(),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "session_path": _string_schema(),
            "refreshed": _boolean_schema(),
        },
        required=[
            "project_root",
            "integration",
            "backend_url",
            "oauth_base_url",
            "session_path",
            "refreshed",
        ],
    )


def _api_context_output_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "project_root": _string_schema(),
            "api_base": _string_schema(),
            "notion_version": _string_schema(),
            "docs_reference": _string_schema(),
            "docs_versioning": _string_schema(),
            "access_token": _string_schema(),
            "credential_provider": _nullable(_string_schema(enum=["keyring", "1password"])),
            "headers": _object_schema({}, additional_properties=_string_schema()),
            "workspace_name": _nullable(_string_schema()),
            "workspace_id": _nullable(_string_schema()),
            "bot_id": _nullable(_string_schema()),
            "default_resource_alias": _nullable(_string_schema()),
            "default_binding": _nullable(_binding_resource_schema()),
            "resources": _array_schema(_binding_resource_schema()),
            "binding_model": _string_schema(),
            "selection_scope_note": _string_schema(),
            "binding_path": _string_schema(),
            "session_path": _string_schema(),
            "refresh_supported": _boolean_schema(),
            "refresh_tool": _string_schema(),
            "usage": _string_schema(),
            "curl_example": _nullable(_string_schema()),
        },
        required=[
            "project_root",
            "api_base",
            "notion_version",
            "docs_reference",
            "docs_versioning",
            "access_token",
            "headers",
            "resources",
            "binding_model",
            "selection_scope_note",
            "binding_path",
            "session_path",
            "refresh_supported",
            "refresh_tool",
            "usage",
            "curl_example",
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


def _tool_definitions() -> list[types.Tool]:
    resource_ref_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "resource_id_or_url": {"type": "string"},
            "resource_id": {"type": "string"},
            "resource_url": {"type": "string"},
            "resource_type": {"type": "string"},
            "alias": {"type": "string"},
            "selection_scope": {"type": "string", "enum": ["resource", "subtree"]},
        },
    }
    return [
        _tool(
            name="notion_status",
            title="Notion Project Status",
            description="Read the current auth, binding, and pending-handoff status for this project.",
            properties={"project_root": {"type": "string"}},
            output_schema=_status_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            description="Return the setup guide for the hosted public integration and its Cloudflare Worker backend.",
            properties={},
            output_schema=_setup_guide_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_auth_browser",
            title="Start Browser Auth",
            description=(
                "Open the official Notion public-integration consent flow in a browser and start a localhost handoff "
                "listener for this project. The browser flow completes asynchronously, so call notion_status after "
                "you finish consent and selection. The default localhost listener timeout is "
                f"{DEFAULT_BROWSER_AUTH_TIMEOUT_SECONDS} seconds, and the default page_limit is "
                f"{DEFAULT_BROWSER_AUTH_PAGE_LIMIT}. If called with open_browser=false, this tool switches to the "
                "headless flow instead."
            ),
            properties={
                "project_root": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
                "open_browser": {"type": "boolean"},
                "page_limit": {"type": "integer"},
            },
            output_schema=_auth_browser_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_start_headless_auth",
            title="Start Headless Auth",
            description="Create a headless public-integration auth URL. The user can finish auth in any browser and then paste the returned handoff bundle back into Codex.",
            properties={
                "project_root": {"type": "string"},
                "page_limit": {"type": "integer"},
            },
            output_schema=_start_headless_auth_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_complete_headless_auth",
            title="Complete Headless Auth",
            description="Finish a headless public-integration auth flow using the handoff bundle shown by the Worker callback page.",
            properties={
                "project_root": {"type": "string"},
                "handoff_bundle": {"type": "string"},
            },
            required=["handoff_bundle"],
            output_schema=_auth_completion_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_finalize_pending_auth",
            title="Finalize Pending Browser Auth",
            description="Persist a pending browser auth or browser selection handoff that has already reached the local MCP server. Use this after notion_status reports pending_handoff_ready=true.",
            properties={"project_root": {"type": "string"}},
            output_schema=_auth_completion_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
        ),
        _tool(
            name="notion_refresh_session",
            title="Refresh Notion Session",
            description="Refresh the saved Notion public-integration session for this project through the Worker backend.",
            properties={"project_root": {"type": "string"}},
            output_schema=_refresh_session_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_list_saved_credentials",
            title="List Saved Credentials",
            description="List saved shared Notion credentials for this integration from the configured keyring or 1Password provider, without re-running OAuth.",
            properties={
                "project_root": {"type": "string"},
                "credential_provider": {"type": "string", "enum": ["keyring", "1password"]},
            },
            output_schema=_list_saved_credentials_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
        _tool(
            name="notion_attach_saved_credential",
            title="Attach Saved Credential",
            description="Attach one previously saved shared Notion credential to this project so you can bind resources without re-running OAuth. If multiple saved credentials exist, pass credential_ref explicitly.",
            properties={
                "project_root": {"type": "string"},
                "credential_ref": {"type": "string"},
                "credential_provider": {"type": "string", "enum": ["keyring", "1password"]},
                "clear_bindings": {"type": "boolean"},
            },
            output_schema=_attach_saved_credential_output_schema(),
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
        _tool(
            name="notion_selection_browser",
            title="Reopen Selection UI",
            description=(
                "Open the hosted Notion resource-selection UI using the current saved session or a saved shared "
                "credential, without re-running OAuth consent. This can replace the project's current bindings, so "
                "pass replace_existing_bindings=true when rebinding an already-configured project. If called with "
                "open_browser=false, this tool switches to a headless selection URL."
            ),
            properties={
                "project_root": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
                "open_browser": {"type": "boolean"},
                "page_limit": {"type": "integer"},
                "credential_ref": {"type": "string"},
                "credential_provider": {"type": "string", "enum": ["keyring", "1password"]},
                "replace_existing_bindings": {"type": "boolean"},
            },
            output_schema=_selection_browser_output_schema(),
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
        _tool(
            name="notion_clear_project_auth",
            title="Clear Project Auth",
            description="Remove the saved project-local Notion session, and optionally the bound resources too. Shared keyring or 1Password credentials are not deleted.",
            properties={
                "project_root": {"type": "string"},
                "clear_bindings": {"type": "boolean"},
            },
            output_schema=_clear_project_auth_output_schema(),
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
        _tool(
            name="notion_bind_resources",
            title="Bind Resources",
            description="Bind one or more existing Notion pages or data sources to the current project using the saved access token. Pass selection_scope='subtree' to bind a root resource and treat nested content under it as included.",
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
            description="Return the current public-integration access token, official API headers, and bound resource IDs so the agent can call the original Notion REST API directly.",
            properties={"project_root": {"type": "string"}},
            output_schema=_api_context_output_schema(),
            read_only=True,
            destructive=False,
            idempotent=True,
            open_world=True,
        ),
    ]


def _handlers() -> dict[str, ToolHandler]:
    return {
        "notion_status": lambda args: status(project_root=args.get("project_root")),
        "notion_setup_guide": lambda args: _setup_guide_tool_payload(),
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
        "notion_finalize_pending_auth": lambda args: finalize_pending_auth(
            project_root=args.get("project_root"),
        ),
        "notion_refresh_session": lambda args: refresh_session(project_root=args.get("project_root")),
        "notion_list_saved_credentials": lambda args: list_saved_credentials(
            project_root=args.get("project_root"),
            credential_provider=args.get("credential_provider"),
        ),
        "notion_attach_saved_credential": lambda args: attach_saved_credential(
            project_root=args.get("project_root"),
            credential_ref=args.get("credential_ref"),
            credential_provider=args.get("credential_provider"),
            clear_bindings=bool(args.get("clear_bindings", False)),
        ),
        "notion_selection_browser": lambda args: selection_browser(
            project_root=args.get("project_root"),
            timeout_seconds=args.get("timeout_seconds"),
            open_browser=bool(args.get("open_browser", True)),
            page_limit=args.get("page_limit"),
            credential_ref=args.get("credential_ref"),
            credential_provider=args.get("credential_provider"),
            replace_existing_bindings=bool(args.get("replace_existing_bindings", False)),
        ),
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


def _resource_definitions() -> list[types.Resource]:
    return [
        types.Resource(
            name="notion_setup_guide",
            title="Notion Setup Guide",
            uri=SETUP_GUIDE_RESOURCE_URI,
            description="Static setup guidance for the shared Notion access-broker and the Agent Labbook MCP server.",
            mimeType="text/markdown",
        ),
        types.Resource(
            name="notion_project_status",
            title="Notion Project Status",
            uri=STATUS_RESOURCE_URI,
            description="Read-only JSON snapshot of the current project's auth, binding, and pending-handoff state.",
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
            description="Recommended workflow for connecting the current project to Notion while preferring credential reuse over a new OAuth flow.",
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
            description="Recommended workflow for checking bindings and then calling the official Notion API with the project's saved access token.",
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


def _prompt_result(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    project_suffix = _prompt_project_suffix(arguments)
    if name == "notion_connect_project":
        text = "\n".join(
            [
                "Connect this project to Notion with the MCP server's preferred workflow.",
                project_suffix,
                f"1. Read {STATUS_RESOURCE_URI} or call notion_status.",
                "2. If notion_status reports saved_credentials_error or credential_provider_diagnostics_error, stop and fix the local notion-access-broker helper setup before starting OAuth.",
                "3. If saved shared credentials already exist, prefer notion_list_saved_credentials and notion_attach_saved_credential before starting OAuth again.",
                "4. Check notion_status.preferred_browser_flow and notion_status.recommended_open_browser before starting any browser flow.",
                "5. If no reusable credential exists, use notion_auth_browser for same-machine browser flows or notion_start_headless_auth for remote/headless flows.",
                "6. If you reopen notion_selection_browser in a remote or SSH session, pass open_browser=false unless you are sure the browser can reach the MCP host's localhost callback.",
                "7. After the browser says the project is connected, call notion_status again.",
                "8. If notion_status reports pending_handoff_ready=true, call notion_finalize_pending_auth.",
                "9. If the browser shows a handoff bundle instead, call notion_complete_headless_auth with that bundle.",
                "10. Once authenticated, bind additional resources only when needed.",
            ]
        )
    elif name == "notion_use_bound_resources":
        text = "\n".join(
            [
                "Use the project's bound Notion resources safely.",
                project_suffix,
                f"1. Read {BINDINGS_RESOURCE_URI} or call notion_list_bindings to understand the current explicit roots and selection_scope values.",
                "2. Call notion_get_api_context only when you are ready to use the official Notion API.",
                "3. Use the returned headers and access token with the official Notion REST API.",
                "4. Treat the access token like a password and avoid echoing it into logs or chat transcripts.",
                "5. If the project is not authenticated or the session is stale, use notion_status to choose the next auth step first.",
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


def _tool_result(payload: dict[str, Any] | str, *, is_error: bool = False) -> types.CallToolResult:
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
) -> types.CallToolResult | ToolSuccessResult:
    handler = _handlers().get(name)
    if handler is None:
        return _tool_result({"error": f"Unknown tool: {name}"}, is_error=True)

    try:
        return handler(arguments or {})
    except LabbookError as exc:
        return _tool_result({"error": str(exc)}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        return _tool_result({"error": f"Internal error: {exc}"}, is_error=True)


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
        text = project_bindings_resource(project_root=project_root)
        mime_type = "application/json"
    else:
        raise LabbookError(f"Unknown resource: {raw_uri}")

    return [ReadResourceContents(content=text, mime_type=mime_type)]


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return _prompt_definitions()


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
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
