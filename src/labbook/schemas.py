"""JSON Schema builders and output schema definitions for MCP tool results.

This module centralises every JSON Schema fragment used by the MCP server
so that ``mcp_server.py`` and ``tools.py`` remain focused on registration
and dispatch logic.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Primitive schema builders
# ---------------------------------------------------------------------------


def string(
    *,
    enum: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    if enum:
        schema["enum"] = enum
    return schema


def integer(*, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if description:
        schema["description"] = description
    return schema


def boolean(*, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "boolean"}
    if description:
        schema["description"] = description
    return schema


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional: bool | dict[str, Any] = True,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional,
    }
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# Reusable composite schemas
# ---------------------------------------------------------------------------


def binding_resource() -> dict[str, Any]:
    return obj(
        {
            "alias": string(),
            "resource_id": string(),
            "resource_type": string(enum=["page", "data_source"]),
            "resource_url": nullable(string()),
            "title": string(),
            "source": string(),
            "bound_at": string(),
            "selection_scope": string(enum=["resource", "subtree"]),
        },
        required=["alias", "resource_id", "resource_type", "selection_scope"],
    )


def storage_backend() -> dict[str, Any]:
    return obj(
        {
            "backend": string(enum=["keychain", "1password"]),
            "display_name": string(),
            "available": boolean(),
            "selected_by_default": boolean(),
            "reason": nullable(string()),
            "details": obj({}, additional=True),
        },
        required=[
            "backend",
            "display_name",
            "available",
            "selected_by_default",
            "details",
        ],
    )


def secret_plan() -> dict[str, Any]:
    return obj(
        {
            "mode": string(enum=["env", "keychain", "1password"]),
            "reason": string(),
        },
        required=["mode", "reason"],
    )


def binding_option() -> dict[str, Any]:
    return obj(
        {
            "mode": string(
                enum=["wait_for_auth", "url", "local_browser", "manual_mcp"]
            ),
            "label": string(),
            "available": boolean(),
            "recommended": boolean(),
            "reason": string(),
        },
        required=["mode", "label", "available", "recommended", "reason"],
    )


def binding_recommendation() -> dict[str, Any]:
    return obj(
        {
            "mode": string(
                enum=["wait_for_auth", "url", "local_browser", "manual_mcp"]
            ),
            "reason": string(),
        },
        required=["mode", "reason"],
    )


def binding_resource_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "resource_id_or_url": {
                "type": "string",
                "description": "Notion resource UUID or full URL.",
            },
            "resource_id": {
                "type": "string",
                "description": "Notion resource UUID.",
            },
            "resource_url": {
                "type": "string",
                "description": "Full Notion URL of the resource.",
            },
            "resource_type": {
                "type": "string",
                "enum": ["page", "data_source", "database"],
                "description": "Type of Notion resource.",
            },
            "title": {
                "type": "string",
                "description": "Human-readable title for the resource.",
            },
            "alias": {
                "type": "string",
                "description": "Short alias used to reference this binding locally.",
            },
            "selection_scope": {
                "type": "string",
                "enum": ["resource", "subtree"],
                "description": "Whether to bind only this resource or include its entire subtree.",
            },
        },
        "required": ["resource_id_or_url"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Search / discovery result schemas
# ---------------------------------------------------------------------------


def search_result() -> dict[str, Any]:
    return obj(
        {
            "resource_id": string(),
            "resource_type": string(enum=["page", "data_source"]),
            "resource_url": nullable(string()),
            "title": string(),
            "last_edited_time": nullable(string()),
            "parent": nullable(obj({}, additional=True)),
        },
        required=["resource_id", "resource_type", "title"],
    )


def discovery_result() -> dict[str, Any]:
    return obj(
        {
            "resource_id": string(),
            "resource_type": string(enum=["page", "data_source"]),
            "resource_url": nullable(string()),
            "title": string(),
            "last_edited_time": nullable(string()),
            "parent": nullable(obj({}, additional=True)),
            "parent_type": nullable(string()),
            "parent_id": nullable(string()),
            "parent_database_id": nullable(string()),
            "discovered_parent_id": nullable(string()),
            "discovered_root_id": nullable(string()),
            "discovered_depth": nullable(integer()),
        },
        required=["resource_id", "resource_type", "title"],
    )


# ---------------------------------------------------------------------------
# Tool output schemas
# ---------------------------------------------------------------------------

_STORAGE_ENUM = ["keychain", "1password"]
_TOKEN_SOURCE_ENUM = ["env", "keychain", "1password"]


def status_output() -> dict[str, Any]:
    return obj(
        {
            "integration": string(),
            "project_root": string(),
            "authentication_mode": string(enum=["internal_integration"]),
            "authenticated": boolean(),
            "token_source": nullable(string(enum=_TOKEN_SOURCE_ENUM)),
            "storage": nullable(string(enum=_STORAGE_ENUM)),
            "env_token_present": boolean(),
            "keyring_backend": string(),
            "keyring_error": nullable(string()),
            "onepassword_error": nullable(string()),
            "storage_error": nullable(string()),
            "storage_options": array(storage_backend()),
            "storage_default": nullable(string(enum=_STORAGE_ENUM)),
            "secret_plan": secret_plan(),
            "storage_choice_required": boolean(),
            "workspace_name": nullable(string()),
            "workspace_id": nullable(string()),
            "bot_id": nullable(string()),
            "bot_owner_type": nullable(string()),
            "configured_at": nullable(string()),
            "session_path": string(),
            "session_exists": boolean(),
            "bindings_path": string(),
            "bindings_configured": boolean(),
            "bindings_count": integer(),
            "recommended_action": string(),
            "authentication_hint": string(),
            "storage_hint": string(),
            "binding_hint": string(),
            "likely_headless": boolean(),
            "binding_recommendation": binding_recommendation(),
            "binding_options": array(binding_option()),
            "setup_resource_uri": string(),
            "available_env_var": string(),
            "recommended_local_command": nullable(string()),
            "notion_integrations_url": string(),
            "notion_docs_url": string(),
            "notion_version": string(),
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
            "recommended_local_command",
            "notion_integrations_url",
            "notion_docs_url",
            "notion_version",
        ],
    )


def setup_guide_output() -> dict[str, Any]:
    return obj(
        {
            "guide_markdown": string(),
            "resource_uri": string(),
        },
        required=["guide_markdown", "resource_uri"],
    )


def prepare_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "notion_integrations_url": string(),
            "notion_docs_url": string(),
            "browser_opened": boolean(),
            "open_browser_attempted": boolean(),
            "storage_options": array(storage_backend()),
            "storage_default": nullable(string(enum=_STORAGE_ENUM)),
            "storage_choice_required": boolean(),
            "secret_label": string(),
            "setup_steps": array(string()),
            "recommended_next_action": string(),
            "recommended_local_command": nullable(string()),
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
            "recommended_local_command",
        ],
    )


def configure_output() -> dict[str, Any]:
    return obj(
        {
            "ok": boolean(),
            "authentication_mode": string(enum=["internal_integration"]),
            "project_root": string(),
            "token_source": string(enum=_STORAGE_ENUM),
            "storage": string(enum=_STORAGE_ENUM),
            "workspace_name": nullable(string()),
            "workspace_id": nullable(string()),
            "bot_id": nullable(string()),
            "bot_owner_type": nullable(string()),
            "storage_options": array(storage_backend()),
            "recommended_next_action": string(),
            "op_vault": nullable(string()),
            "op_item_title": nullable(string()),
            "keyring_backend": string(),
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


def search_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "query": nullable(string()),
            "page_size": integer(),
            "result_count": integer(),
            "results": array(search_result()),
        },
        required=["project_root", "page_size", "result_count", "results"],
    )


def discovery_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "root_resource": discovery_result(),
            "page_size": integer(),
            "mode": string(enum=["shallow", "deep"]),
            "partial": boolean(),
            "result_count": integer(),
            "results": array(discovery_result()),
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


def bindings_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "default_resource_alias": nullable(string()),
            "resource_count": integer(),
            "resources": array(binding_resource()),
        },
        required=["project_root", "resource_count", "resources"],
    )


def binding_browser_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "chooser_url": string(),
            "browser_opened": boolean(),
            "open_browser_attempted": boolean(),
            "timeout_seconds": integer(),
            "page_size": integer(),
            "workspace_name": nullable(string()),
            "recommended_next_action": string(),
            "headless_flow_hint": string(),
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


def clear_project_auth_output() -> dict[str, Any]:
    return obj(
        {
            "ok": boolean(),
            "project_root": string(),
            "storage": nullable(string(enum=_STORAGE_ENUM)),
            "session_cleared": boolean(),
            "stored_secret_deleted": boolean(),
            "bindings_cleared": boolean(),
            "env_token_still_present": boolean(),
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


def api_context_output() -> dict[str, Any]:
    return obj(
        {
            "project_root": string(),
            "authentication_mode": string(enum=["internal_integration"]),
            "token_source": string(enum=_TOKEN_SOURCE_ENUM),
            "storage": nullable(string(enum=_STORAGE_ENUM)),
            "access_token": string(),
            "api_base_url": string(),
            "notion_version": string(),
            "headers": obj({}, additional=True),
            "workspace_name": nullable(string()),
            "workspace_id": nullable(string()),
            "bot_id": nullable(string()),
            "bound_resources": array(binding_resource()),
            "selection_scope_note": string(),
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
