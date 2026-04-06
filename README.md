# Agent Labbook

Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. It handles Notion auth for a project, lets you choose which pages or data sources belong to that project, stores bindings in `.labbook/`, keeps long-lived tokens in a shared credential provider, and returns the API context your agent needs to call Notion directly.

It is not a full Notion SDK. After setup, your agent should use the official Notion API directly.

## Main Features

- Connect a repo to specific Notion pages or data sources for an agent.
- Reuse saved Notion credentials across projects.
- Bind only the pages or data sources a project should use.
- Return access tokens, headers, and bound resource IDs for the official Notion API.
- Work with Codex, Claude Code, and other MCP-capable clients.

## Install

Requirements:
- Python 3.10 or newer
- `uv`
- a Codex, Claude Code, or another MCP-capable client

Recommended:

```bash
codex mcp add labbook -- uvx agent-labbook mcp
claude mcp add --scope project labbook -- uvx agent-labbook mcp
```

Or use the checked-in [`.mcp.json`](./.mcp.json) when this repo is the MCP source.

## Use

1. Read `labbook://agent-labbook/project/status` or run `notion_status`.
2. Reuse a saved credential when available, or start auth with `notion_auth_browser` or `notion_start_headless_auth`.
3. Choose the pages or data sources that belong to the project, then finish the handoff with `notion_status` and `notion_finalize_pending_auth` when needed.
4. Read `labbook://agent-labbook/project/bindings` or run `notion_list_bindings` to inspect the bound roots.
5. Run `notion_get_api_context` and use the returned token, headers, and resource IDs with the official Notion API.

If your content already exists as markdown, prefer Notion's markdown content APIs:

- `POST /v1/pages` with `markdown`
- `GET /v1/pages/{page_id}/markdown`
- `PATCH /v1/pages/{page_id}/markdown`

Reference: `https://developers.notion.com/guides/data-apis/working-with-markdown-content`

## Notes

- `.labbook/` should never be committed.
- This repo handles auth and project binding, not general Notion API wrapping.
- For self-hosting, see [`docs/self-host.md`](./docs/self-host.md).
- For versioning details, see [`docs/versioning.md`](./docs/versioning.md).
