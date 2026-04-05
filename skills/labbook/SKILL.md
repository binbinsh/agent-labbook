---
name: agent-labbook
description: Authorize the hosted Notion public integration, bind project-local resources, and then use the official Notion REST API directly. Use when the agent needs real Notion access from Codex, Claude Code, or another MCP-capable runtime through the hosted Agent Labbook app and shared Notion OAuth backends without a stateful backend.
---

# Agent Labbook

## Purpose

Agent Labbook handles Notion auth and project bindings for agents.

Use it to:

- complete the hosted Notion public-integration flow through `https://superplanner.ai/notion/agent-labbook`
- rely on the shared OAuth backend at `https://superplanner.ai/notion/oauth`
- keep long-lived tokens in a shared local credential provider instead of the repo
- store project-local bindings under `.labbook/`
- return API context such as the access token, headers, and bound resource IDs

It is not a general Notion wrapper or task-management layer.

## Connect Decision Rule

Before choosing any connect flow yourself:

1. Call `notion_status`.
2. Inspect `notion_status.connect_decision`.
3. If the user has not already chosen `scope_mode` and `browser_mode`, ask those two questions first.
4. If the client supports interactive prompts, map `connect_decision.questions` into those prompts.
5. Otherwise show `connect_decision.manual_prompt_markdown` verbatim and wait for the user's answer.

Do not silently choose between:

- `bind_existing_scope` and `expand_oauth_scope`
- `local_browser` and `headless`

Only skip these questions when the user has already made the choice explicitly.

## Workflow

1. Call `notion_status` or read `labbook://agent-labbook/project/status`.
2. If `saved_credentials_error` or `credential_provider_diagnostics_error` is present, stop and fix the local `notion-access-broker` setup before starting OAuth.
3. If reusable saved credentials exist, prefer `notion_list_saved_credentials` and `notion_attach_saved_credential` before starting a new OAuth flow.
4. If the user chose `bind_existing_scope`, use `notion_selection_browser` to reopen the hosted chooser within the current integration scope.
5. If the user chose `expand_oauth_scope`, use `notion_auth_browser` for same-machine browser flows or `notion_start_headless_auth` for remote/headless flows.
6. After the browser says the project is connected, call `notion_status` again.
7. If `pending_handoff_ready=true`, call `notion_finalize_pending_auth`.
8. If the browser shows a handoff bundle instead, call `notion_complete_headless_auth`.
9. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings` when you need the current explicit roots and aliases.
10. If the project still needs more bindings, call `notion_bind_resources`.
11. Call `notion_get_api_context`.
12. Use the official Notion API directly with the returned token, headers, and resource IDs.

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
