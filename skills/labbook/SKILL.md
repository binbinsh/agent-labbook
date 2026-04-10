---
name: agent-labbook
description: Configure a Notion Internal Integration secret, bind project-local resources, and then use the official Notion REST API directly. Use when the agent needs real Notion access from Codex, Claude Code, or another MCP-capable runtime through the local Notion Agent Labbook MCP server.
---

# Notion Agent Labbook

## Purpose

Notion Agent Labbook handles local Notion configuration and project bindings for agents.

Use it to:

- open the Notion integrations dashboard with `notion_prepare_internal_integration`
- configure a Notion Internal Integration secret with `notion_configure_internal_integration`
- rely on `NOTION_AGENT_LABBOOK_TOKEN` as an environment override when needed
- store project-local bindings under `.labbook/`
- return API context such as the access token, headers, and bound resource IDs

It is not a general Notion wrapper or task-management layer.

## Workflow

1. Call `notion_status` or read `labbook://agent-labbook/project/status`.
2. If the project is not authenticated, call `notion_prepare_internal_integration`.
3. Inspect `storage_options`, `storage_default`, `storage_choice_required`, and `secret_plan`.
4. If `storage_choice_required=true`, ask the user whether they want `keychain` or `1password`.
5. Use `notion_configure_internal_integration` to validate and store the secret with the chosen `storage` value.
6. Prefer `op_vault` and `op_item_title` only when the user explicitly cares about where the 1Password item is stored.
7. Remind the user to share the target pages or data sources with the integration bot inside Notion.
8. Read `notion_status.binding_recommendation` and `notion_status.binding_options` before choosing a binding UX.
9. Ask whether the user can paste exact Notion links. If yes, prefer `notion_bind_resource_urls`.
10. If not, prefer `notion_open_binding_browser` on desktop-capable environments.
11. In headless environments, use `notion_search_resources`, `notion_discover_children`, and then `notion_bind_resources`.
12. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings` when you need the current explicit roots and aliases.
13. Call `notion_get_api_context` only when you are ready to use the official Notion API.
14. Use the official Notion API directly with the returned token, headers, and resource IDs.

## Direct API Rules

- Prefer the official REST API at `https://api.notion.com/v1`.
- Prefer the latest official API docs at `https://developers.notion.com/reference/intro` before guessing an endpoint shape.
- If request or response fields might depend on API versioning, check `https://developers.notion.com/reference/versioning`.
- When your source content is already markdown, prefer Notion's markdown content APIs instead of manually expanding block children. Use `POST /v1/pages` with `markdown` to create pages, `GET /v1/pages/{page_id}/markdown` to read page content as markdown, and `PATCH /v1/pages/{page_id}/markdown` to update page content. See `https://developers.notion.com/guides/data-apis/working-with-markdown-content`.
- Treat this integration as auth and binding infrastructure, not as a content API.
- Prefer the MCP resources for read-only context and the MCP tools for side effects.
- Tool results are structured and schema-backed; prefer their `structuredContent` over re-parsing display text.
- Do not assume Notion is connected for the current project until `notion_status` confirms it.
- Do not use `notion_get_api_context` as a health check; prefer `notion_status` and `notion_search_resources`.
- Reuse aliases from `notion_list_bindings` so later sessions stay consistent.
- Project-local state lives under `.labbook/` and should never be committed. The integration secret itself should come from system keychain, 1Password, or the process environment, not `.labbook/session.json`.
