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
- prefer `agent-labbook configure-secret --storage keychain` for local hidden input
- rely on `NOTION_AGENT_LABBOOK_TOKEN` as an environment override when needed
- store project-local bindings under `.labbook/`
- return API context such as the access token, headers, and bound resource IDs

It is not a general Notion wrapper or task-management layer.

## Workflow

1. Call `notion_status` or read `labbook://agent-labbook/project/status`.
2. If the project is not authenticated, call `notion_prepare_internal_integration`.
3. Use `agent-labbook configure-secret --storage keychain` on a workstation so the secret is captured via a local hidden prompt and stored in the system keychain.
4. Use `notion_configure_internal_integration` only when the caller can safely provide the secret directly.
5. Remind the user to share the target pages or data sources with the integration bot inside Notion.
6. Prefer `notion_bind_resource_urls` for exact links, `notion_start_binding_server` to hand the user a chooser URL, or `notion_search_resources` plus `notion_discover_children` in headless environments.
7. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings` when you need the current explicit roots and aliases.
8. Call `notion_get_api_context` only when you are ready to use the official Notion API.
9. Before making direct API calls, read [references/notion-api.md](references/notion-api.md) for the current Notion API shape that matters to this skill.
10. Use the official Notion API directly with the returned token, headers, and resource IDs.

## Direct API Rules

- Prefer the official REST API at `https://api.notion.com/v1`.
- Read [references/notion-api.md](references/notion-api.md) when you need current endpoint shapes, version notes, or limitations.
- If request or response fields might depend on API versioning, check `https://developers.notion.com/reference/versioning`.
- When your source content is already markdown, prefer Notion's markdown content APIs instead of manually expanding block children.
- Treat this integration as auth and binding infrastructure, not as a content API.
- Prefer the MCP resources for read-only context and the MCP tools for side effects.
- Tool results are structured and schema-backed; prefer their `structuredContent` over re-parsing display text.
- Do not assume Notion is connected for the current project until `notion_status` confirms it.
- Do not use `notion_get_api_context` as a health check; prefer `notion_status` and `notion_search_resources`.
- Reuse aliases from `notion_list_bindings` so later sessions stay consistent.
- Project-local state lives under `.labbook/` and should never be committed. The integration secret itself should come from the system keychain or the process environment, not `.labbook/session.json`.
