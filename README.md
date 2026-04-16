# Notion Agent Labbook

[![CI](https://github.com/binbinsh/agent-labbook/actions/workflows/ci.yml/badge.svg)](https://github.com/binbinsh/agent-labbook/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-labbook)](https://pypi.org/project/agent-labbook/)
[![Python](https://img.shields.io/pypi/pyversions/agent-labbook)](https://pypi.org/project/agent-labbook/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

`agent-labbook` is a local MCP server that lets Codex, Claude Code, OpenCode, and other MCP clients use a Notion Internal Integration directly.

No OAuth, no hosted broker, no cloud worker. You connect Notion once, store the Internal Integration secret locally, bind the pages or data sources you want, and then call the official Notion API.

## What It Does

- Stores your Notion Internal Integration secret in the local system keychain
- Lets an MCP client search, discover, and bind specific Notion pages or data sources
- Returns API headers and bound resource IDs for direct Notion API calls
- Provides a browser-based resource chooser for desktop environments

## 1. Create A Notion Internal Integration

Create a Notion Internal Integration here:

- Notion integrations dashboard: [notion.so/my-integrations](https://www.notion.so/my-integrations)
- Notion guide: [Create a Notion integration](https://developers.notion.com/guides/get-started/create-a-notion-integration)

After creating it:

1. Copy the `Internal Integration Secret` from the `Configuration` tab.
2. Share the target Notion pages or data sources with the integration.

## 2. Set The Token

Recommended on a workstation:

```bash
uvx agent-labbook configure-secret --storage keychain
```

CI or temporary override:

```bash
export NOTION_AGENT_LABBOOK_TOKEN=secret_xxx
```

Default policy:

- `keychain` is the default local backend
- `NOTION_AGENT_LABBOOK_TOKEN` is for CI or temporary overrides

## 3. Install The MCP Server

Codex:

```bash
codex mcp add labbook -- uvx agent-labbook mcp
```

Or add it directly to your `~/.codex/config.toml`:

```toml
[mcp_servers.labbook]
command = "uvx"
args = ["agent-labbook", "mcp"]
```

Claude Code (project scope, writes to `.mcp.json`):

```bash
claude mcp add --scope local labbook -- uvx agent-labbook mcp
```

Claude Code (user scope, writes to `~/.claude.json`):

```bash
claude mcp add --scope user labbook -- uvx agent-labbook mcp
```

OpenCode or other MCP clients:

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

You can also generate the config with:

```bash
uvx agent-labbook print-mcp-config
```

## 4. Use It

Typical flow:

1. Call `notion_status` to check the current project state.
2. Bind resources with `notion_bind_resource_urls` for exact links, `notion_open_binding_browser` on desktop, or `notion_search_resources` plus `notion_discover_children` in headless environments.
3. Call `notion_get_api_context` only when you are ready to use the official Notion API.

## MCP Surface Reference

### Tools (12)

| Tool | Description | Read-only | Destructive |
|------|-------------|-----------|-------------|
| `notion_status` | Read the current Internal Integration auth, storage backend, and bindings status for this project. | Yes | No |
| `notion_setup_guide` | Return the setup guide for the Internal Integration workflow. | Yes | No |
| `notion_prepare_internal_integration` | Open the Notion integrations dashboard and detect available local storage backends before collecting the Internal Integration Secret. | No | No |
| `notion_configure_internal_integration` | Validate and store a Notion Internal Integration secret for this project. | No | No |
| `notion_search_resources` | Search the pages and data sources that the Internal Integration bot can access. | Yes | No |
| `notion_discover_children` | Inspect the immediate child pages or entries beneath a specific page or data source. | Yes | No |
| `notion_bind_resource_urls` | Bind one or more Notion page or data source URLs directly. | No | No |
| `notion_bind_resources` | Bind one or more Notion pages or data sources by reference. | No | No |
| `notion_open_binding_browser` | Start a local browser-based chooser for selecting Notion roots. | No | No |
| `notion_list_bindings` | List the Notion resources currently bound to this project. | Yes | No |
| `notion_get_api_context` | Return the Internal Integration secret, official Notion API headers, and bound resource IDs for direct API calls. | Yes | No |
| `notion_clear_project_auth` | Remove the saved project-local session and delete the stored keychain secret. | No | Yes |

### Resources (3)

| Resource | URI | MIME Type | Description |
|----------|-----|-----------|-------------|
| Notion Setup Guide | `labbook://setup-guide` | `text/markdown` | Static setup guidance for using a Notion Internal Integration secret. |
| Notion Project Status | `labbook://project/status` | `application/json` | Read-only JSON snapshot of the current project's auth, storage backend, and bindings state. |
| Notion Project Bindings | `labbook://project/bindings` | `application/json` | Read-only JSON snapshot of the current project's bound Notion resources. |

### Resource Templates (2)

| Template | URI Pattern | MIME Type | Description |
|----------|-------------|-----------|-------------|
| Project Status By Root | `labbook://project/status?project_root={project_root}` | `application/json` | Read-only JSON project status for an explicit project root. |
| Project Bindings By Root | `labbook://project/bindings?project_root={project_root}` | `application/json` | Read-only JSON bindings for an explicit project root. |

### Prompts (2)

| Prompt | Description |
|--------|-------------|
| `notion_connect_project` | Recommended workflow for connecting the current project to Notion with an Internal Integration secret. |
| `notion_use_bound_resources` | Recommended workflow for checking bindings and calling the official Notion API with the project's configured secret. |

## CLI Commands

| Command | Description |
|---------|-------------|
| `agent-labbook mcp` | Run the MCP stdio server. |
| `agent-labbook configure-secret` | Prompt for the Notion Internal Integration secret and store it locally. Supports `--storage`. |
| `agent-labbook doctor` | Inspect local Notion Agent Labbook state and print diagnostics as JSON. |
| `agent-labbook print-mcp-config` | Print a reusable `uvx`-based MCP server config snippet. |

## Notes

- `.labbook/` stores project-local metadata and bindings, not the secret itself.
- `notion_get_api_context` returns the secret. Use it only for real API calls.
- `NOTION_AGENT_LABBOOK_TOKEN` overrides stored local credentials for the current process.
- The MCP server runs over stdio transport only. No HTTP/SSE transport is exposed.

## License

[MIT](./LICENSE)
