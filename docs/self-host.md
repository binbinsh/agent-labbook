# Local Setup Notes

This project no longer depends on a hosted OAuth service, a Cloudflare Worker, or any browser handoff flow. The entire connection model is local:

- `.labbook/` stores project metadata and bindings
- the Notion Internal Integration secret is either read from `NOTION_AGENT_LABBOOK_TOKEN` or saved in the local system keychain or 1Password
- the MCP server calls the official Notion API directly

## What You Need

- Python 3.10+
- `uv`
- a local keyring backend if you want to persist the secret in system keychain
- `op` with an authenticated 1Password account if you want to persist the secret in 1Password
- a Notion Internal Integration that has been shared with the pages or data sources you want the project to access

## Secret Storage Choices

The current MCP surface uses these names:

- `storage=keychain`
  Save the secret in the local system credential store through Python `keyring`.
- `storage=1password`
  Save the secret in 1Password through the `op` CLI.
- `NOTION_AGENT_LABBOOK_TOKEN`
  Use an environment override instead of persistent local storage.

To decide which one to use, inspect:

- `notion_status.storage_options`
- `notion_status.storage_default`
- `notion_status.storage_choice_required`
- `notion_status.secret_plan`

## Local MCP Configuration

Use a config like this:

```json
{
  "mcpServers": {
    "labbook": {
      "command": "uvx",
      "args": [
        "agent-labbook",
        "mcp"
      ]
    }
  }
}
```

If you want an environment-only secret instead of keyring storage:

```json
{
  "mcpServers": {
    "labbook": {
      "command": "uvx",
      "args": [
        "agent-labbook",
        "mcp"
      ],
      "env": {
        "NOTION_AGENT_LABBOOK_TOKEN": "secret_xxx"
      }
    }
  }
}
```

If you prefer 1Password, keep the MCP config simple and let the runtime store the secret interactively through `notion_configure_internal_integration`. Only use `NOTION_AGENT_LABBOOK_TOKEN` when you deliberately want process-scoped secret injection.

## Recommended Validation Flow

1. Read `labbook://agent-labbook/project/status` or call `notion_status`.
2. If the project is not authenticated, run `notion_prepare_internal_integration`.
3. If `storage_choice_required=true`, ask the user whether they want `keychain` or `1password`.
4. Configure the Internal Integration secret with `notion_configure_internal_integration` or set `NOTION_AGENT_LABBOOK_TOKEN`.
5. Share the target pages or data sources with the integration bot in Notion.
6. If you already know the exact Notion links, call `notion_bind_resource_urls`.
7. On desktop machines, use `notion_open_binding_browser`.
8. On headless machines, prefer `notion_bind_resource_urls` when the user can paste exact links. Otherwise combine `notion_search_resources`, `notion_discover_children`, and `notion_bind_resources`.
9. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings`.
10. Call `notion_get_api_context` only when you are ready to use the official Notion API.
