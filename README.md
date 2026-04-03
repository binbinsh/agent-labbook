# Agent Labbook

Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. It starts Notion auth, lets you choose which pages or data sources belong to a project, stores tokens and bindings locally in `.labbook/`, and returns the API context your agent needs to call Notion directly.

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

1. Run `notion_status`.
2. Run `notion_auth_browser`, or `notion_start_headless_auth` if connecting through SSH or another headless environment.
   For browser auth, prefer a long `timeout_seconds` such as `1800` so the agent keeps waiting while you finish Notion consent and resource selection.
3. Choose the Notion pages or data sources for this project.
4. Run `notion_get_api_context`.
5. Use the returned token, headers, and resource IDs with the official Notion API.

## Hosted Backend

The default backend is `https://labbook.superplanner.net`. It is privacy-friendly: it handles OAuth and token refresh without keeping your project tokens or Notion content in server-side storage. Long-lived tokens and bindings stay local in `.labbook/`.

If you want to self-host it, see [`docs/self-host.md`](./docs/self-host.md).

## Notes

- `.labbook/` should never be committed
- this repo handles auth and project binding, not general Notion API wrapping
