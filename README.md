# Notion Agent Labbook

Notion Agent Labbook is a local MCP server that connects a project to Notion through a Notion Internal Integration secret. It stores project metadata in `.labbook/`, can open the Notion integrations dashboard to help the user fetch the secret, lets the user choose between system keychain and 1Password when both are available, lets agents search accessible pages and data sources, and returns the official API context for direct Notion API calls.

This version does not use OAuth, a shared credential broker, or a hosted Worker.

## Main Features

- Open the Notion integrations dashboard and guide the user to the Internal Integration Secret.
- Detect whether system keychain and 1Password are available, and let the user choose.
- Store the secret in the local system keychain, in 1Password, or override it with `NOTION_AGENT_LABBOOK_TOKEN`.
- Search the pages and data sources the bot can access.
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

For OpenCode or other MCP clients, add the following to your `.mcp.json`:

```json
{
  "mcpServers": {
    "labbook": {
      "command": "uvx",
      "args": ["agent-labbook", "mcp"]
    }
  }
}
```

Or generate this config with:

```bash
uvx agent-labbook print-mcp-config
```

Or use the checked-in [`.mcp.json`](./.mcp.json) for local development from a cloned copy of this repo.

## Recommended Flow

1. Read `labbook://agent-labbook/project/status` or run `notion_status`.
2. Run `notion_prepare_internal_integration` to open the Notion integrations dashboard and inspect `storage_options`, `storage_default`, and `storage_choice_required`.
3. Create a Notion Internal Integration, copy its secret from the `Configuration` tab, and share the target pages or data sources with the bot in Notion.
4. Save the secret with `notion_configure_internal_integration`, choosing `storage=keychain` or `storage=1password`, or set `NOTION_AGENT_LABBOOK_TOKEN`.
5. Run `notion_search_resources` to find accessible content.
6. Run `notion_bind_resources` to bind the pages or data sources this project should use.
7. Read `labbook://agent-labbook/project/bindings` or run `notion_list_bindings` to inspect the bound roots.
8. Run `notion_get_api_context` and use the returned token, headers, and resource IDs with the official Notion API.

## Save The Secret

Use `notion_configure_internal_integration` for persistent storage:

- `storage=keychain`
  Default recommendation for local development when system keychain is available.
- `storage=1password`
  Use when the `op` CLI is installed and signed in. You can optionally provide `op_vault` and `op_item_title`.
- `NOTION_AGENT_LABBOOK_TOKEN`
  Use for CI, temporary runs, or environments where no local secret backend is available.

If more than one local backend is available and you omit `storage`, the tool will ask the caller to make an explicit choice instead of guessing.

## Check The Secret

Use `notion_status` or `agent-labbook doctor` to inspect the current setup without retrieving the secret itself.

Important fields:

- `authenticated`
  Whether the project currently has a usable Internal Integration secret.
- `storage`
  Which persistent backend this project is configured to use.
- `secret_plan`
  The recommended secret strategy for this machine right now.
- `storage_options`
  The detected local storage backends and their availability.
- `storage_choice_required`
  Whether the caller should ask the user to choose between keychain and 1Password.

To verify that the secret also has the correct Notion permissions, prefer `notion_search_resources` instead of `notion_get_api_context`.

## Guide Users

The recommended user-facing flow for agents is:

1. Call `notion_status`.
2. If `authenticated=false`, call `notion_prepare_internal_integration`.
3. If `storage_choice_required=true`, ask the user whether they want `keychain` or `1password`.
4. Tell the user to copy the `Internal Integration Secret` from Notion's `Configuration` tab.
5. Call `notion_configure_internal_integration`.
6. Tell the user to share the target pages or data sources with the integration bot.
7. Call `notion_search_resources` and then `notion_bind_resources`.

Do not call `notion_get_api_context` just to check whether the setup worked. That tool returns the secret and should only be used when the client is ready to make real Notion API calls.

If your content already exists as markdown, prefer Notion's markdown content APIs:

- `POST /v1/pages` with `markdown`
- `GET /v1/pages/{page_id}/markdown`
- `PATCH /v1/pages/{page_id}/markdown`

Reference: [Working with Markdown Content](https://developers.notion.com/guides/data-apis/working-with-markdown-content)

## Notes

- `.labbook/` should never be committed.
- This repo handles local configuration and project binding, not general Notion API wrapping.
- The system keychain is the default recommendation. 1Password is supported when the `op` CLI is available and signed in.
- `notion_get_api_context` returns the secret. Treat it as a last-mile API call step, not a health check.
- For local setup notes, see [docs/self-host.md](./docs/self-host.md).
- For versioning details, see [docs/versioning.md](./docs/versioning.md).
