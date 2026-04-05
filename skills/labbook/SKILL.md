---
name: agent-labbook
description: Authorize the hosted Notion public integration, bind project-local resources, and then use the official Notion REST API directly. Use when the agent needs real Notion access from Codex, Claude Code, or another MCP-capable runtime through the hosted Agent Labbook app and shared Notion OAuth backends without a stateful backend.
---

# Agent Labbook

## Purpose

This integration is only responsible for:

- completing a Notion public integration flow through the hosted app Worker at `https://superplanner.ai/notion/agent-labbook`
- relying on the shared OAuth backend at `https://superplanner.ai/notion/oauth`
- storing project bindings in the local project while keeping long-lived tokens in a shared local credential provider
- binding one or more Notion pages or data sources to the current project
- returning direct API context such as the access token, headers, and bound resource IDs
- relying on a privacy-friendly hosted service that only handles OAuth and token refresh

It is not a note-taking or task-management wrapper.

## Default Workflow

1. Read `labbook://agent-labbook/project/status` or call `notion_status` for the current project before assuming any Notion session or bindings exist.
2. If `notion_status` reports `saved_credentials_error` or `credential_provider_diagnostics_error`, fix the local `notion-access-broker` helper setup before starting OAuth. A fresh OAuth flow cannot persist shared credentials without it.
3. If `notion_status` reports saved shared credentials for this integration, prefer `notion_list_saved_credentials` and `notion_attach_saved_credential` before re-running OAuth.
4. If the project needs the browser-based root-page chooser again, call `notion_selection_browser` instead of re-running OAuth.
5. If the project is not authorized yet, call `notion_auth_browser`. It starts a localhost handoff listener and the browser flow completes asynchronously, so call `notion_status` again after the browser says the project is connected.
6. If `notion_status` reports `pending_handoff_ready=true`, call `notion_finalize_pending_auth`. If the browser cannot be opened locally, call `notion_start_headless_auth` and later `notion_complete_headless_auth`.
7. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings` when you need a read-only snapshot of the explicit roots and aliases.
8. If the project still needs more bindings, call `notion_bind_resources`.
9. Call `notion_get_api_context`.
10. Use the official Notion REST API directly with the returned headers and resource IDs.
11. If the exact endpoint is unclear, check the latest official Notion API reference first:
   - `https://developers.notion.com/reference/intro`
   - `https://developers.notion.com/reference/versioning`

## Direct API Rules

- Prefer the official REST API at `https://api.notion.com/v1`.
- Prefer the latest official API docs at `https://developers.notion.com/reference/intro` before guessing an endpoint shape.
- If request or response fields might depend on API versioning, check `https://developers.notion.com/reference/versioning`.
- Treat this integration as auth and binding infrastructure, not as a content API.
- Prefer the MCP resources for read-only context and the MCP tools for side effects.
- Tool results are structured and schema-backed; prefer their `structuredContent` over re-parsing display text.
- Do not assume Notion is connected for the current project until `notion_status` confirms it.
- Use `notion_refresh_session` when a saved token needs to be rotated.
- Reuse aliases from `notion_list_bindings` so later sessions stay consistent.
- Project-local auth state lives under `.labbook/` and should never be committed. Token secrets themselves should come from the shared keyring or 1Password provider, not `.labbook/session.json`.

## Common Pattern

1. `labbook://agent-labbook/project/status` or `notion_status`
2. inspect `saved_credentials_error` and `credential_provider_diagnostics_error` before deciding whether OAuth is even safe
3. `notion_list_saved_credentials` or `notion_auth_browser`
4. `notion_attach_saved_credential` when a reusable shared credential exists
5. `notion_selection_browser` when you want the hosted chooser again without new OAuth consent
6. `notion_status`
7. `notion_finalize_pending_auth` when `pending_handoff_ready=true`
8. `labbook://agent-labbook/project/bindings` or `notion_list_bindings`
9. `notion_bind_resources`
10. `notion_get_api_context`
11. direct Notion API read or write
