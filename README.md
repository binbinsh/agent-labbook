# Agent Labbook

Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. It starts Notion auth, lets you choose which pages or data sources belong to a project, stores project bindings in `.labbook/`, stores long-lived tokens in a shared credential provider, and returns the API context your agent needs to call Notion directly.

Use it when you want to:

- connect a repo to specific Notion pages or data sources for an agent
- let an agent read or update project docs and data sources through the official Notion API
- avoid building your own Notion auth wrapper for coding agents

It is not a full Notion SDK. After setup, your agent should use the official Notion API directly.

## Install

Use Agent Labbook as a local MCP server for the current project.

Requirements:

- Python 3.10 or newer
- `uv`
- a Codex, Claude Code, or other MCP-capable client that can run a local MCP server

Recommended setup:

```bash
codex mcp add labbook -- uvx agent-labbook mcp
claude mcp add --scope project labbook -- uvx agent-labbook mcp
```

- or use the checked-in [`.mcp.json`](./.mcp.json) when the repository itself is the MCP source

## Typical Flow

1. Read `labbook://agent-labbook/project/status` or run `notion_status`.
2. If `notion_status` reports `saved_credentials_error` or `credential_provider_diagnostics_error`, fix the local `notion-access-broker` helper installation before starting OAuth. A fresh OAuth flow cannot persist shared credentials without it.
3. If status shows saved shared credentials for this integration, run `notion_list_saved_credentials` and then `notion_attach_saved_credential` for the workspace you want to reuse.
4. If you want to reopen the hosted root-page selection UI without re-running OAuth, run `notion_selection_browser`.
5. Otherwise run `notion_auth_browser`, or `notion_start_headless_auth` if connecting through SSH or another headless environment.
   For browser auth, pass a long `timeout_seconds` such as `1800` so the background localhost listener stays alive while you finish Notion consent and resource selection.
6. Choose the Notion pages or data sources for this project.
7. Run `notion_status` again after the browser says the project is connected, after attaching the saved credential, or after reopening the selection UI.
8. If `notion_status` reports `pending_handoff_ready=true`, run `notion_finalize_pending_auth`.
9. Read `labbook://agent-labbook/project/bindings` or run `notion_list_bindings` if you want a read-only snapshot of the explicit project roots.
10. Run `notion_get_api_context`.
11. Use the returned token, headers, and resource IDs with the official Notion API.

## MCP Design

- Read-only context is exposed as MCP resources:
  - `labbook://agent-labbook/setup-guide`
  - `labbook://agent-labbook/project/status`
  - `labbook://agent-labbook/project/bindings`
- Reusable workflows are exposed as prompts:
  - `notion_connect_project`
  - `notion_use_bound_resources`
- Mutating actions stay in tools. In particular, `notion_status` is now read-only and `notion_finalize_pending_auth` is the explicit step that persists a pending browser handoff.
- Tools now declare `outputSchema` as well as `inputSchema`, so MCP clients can discover stable structured outputs and the low-level server can validate successful tool payloads before returning them.
- `notion_status` also reports credential provider diagnostics so you can tell whether `1Password` or `keyring` is currently available and which one would be selected by default.

## Hosted Backend

The default app backend is `https://superplanner.ai/notion/agent-labbook`.
The default shared OAuth backend is `https://superplanner.ai/notion/oauth`.

The app backend renders the Labbook selection UI and project-specific routes. The shared OAuth backend handles Notion OAuth, token refresh, and signed handoff bundles without keeping your project tokens or Notion content in server-side storage. Long-lived tokens live in the shared local credential provider selected through the `notion-access-broker` Python helpers, and project bindings stay in `.labbook/`.

## Credential Storage

Agent Labbook no longer writes access tokens or refresh tokens into `.labbook/session.json`.

- If the local `op` CLI is installed and can access 1Password, the default provider automatically prefers `1password`
- If `NOTION_ACCESS_BROKER_1PASSWORD_VAULT` is set, 1Password is pinned to that vault; otherwise it uses the default vault
- If 1Password is unavailable, the default falls back to `keyring`
- You can still force either provider with `NOTION_ACCESS_BROKER_CREDENTIAL_PROVIDER=keyring|1password`
- `notion-access-broker` is installed as a normal package dependency for released builds; `NOTION_ACCESS_BROKER_SRC=/path/to/notion-access-broker[/src]` is only needed when you want a local checkout to override the installed helper during development
- A fresh project can reuse an existing integration credential through `notion_list_saved_credentials` and `notion_attach_saved_credential`
- A project with an attached or reusable credential can reopen the hosted selection UI through `notion_selection_browser` without re-running OAuth consent
- `.labbook/session.json` stores only the selected credential reference and project metadata, not the token secrets themselves

If you want to self-host it, see [`docs/self-host.md`](./docs/self-host.md).
For versioning and migration boundaries, see [`docs/versioning.md`](./docs/versioning.md).

## Notes

- `.labbook/` should never be committed
- this repo handles auth and project binding, not general Notion API wrapping
