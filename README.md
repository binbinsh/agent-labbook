# agent-labbook

Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. It starts Notion auth, lets you choose which pages or data sources belong to a project, stores tokens and bindings locally in `.labbook/`, and returns the API context your agent needs to call Notion directly.

Use it when you want to:

- connect a repo to specific Notion pages or data sources for an agent
- let an agent read or update project docs and data sources through the official Notion API
- avoid building your own Notion auth wrapper for coding agents

It is not a full Notion SDK. After setup, your agent should use the official Notion API directly.

## Install

Agent Labbook is a local `stdio` MCP server built on the official Python MCP SDK. Codex and Claude Code should start it on demand from a command. Do not run a separate long-lived daemon.

Requirements:

- Python 3.10 or newer
- `uv`
- a Codex, Claude Code, or other MCP-capable client that can run a local MCP server

Recommended setup for both Codex and Claude Code:

```bash
uvx --from git+https://github.com/binbinsh/agent-labbook@v0.11.0 agent-labbook mcp
```

This gives you the best behavior for shared use:

- the client auto-starts the MCP server only when needed
- the runtime is isolated from your global Python
- Codex and Claude Code can use the exact same command
- pinning a tag keeps installs reproducible and safer than following a moving branch

If Codex installs this repository as a plugin or MCP repo, the checked-in [`.mcp.json`](./.mcp.json) already uses `uvx --from .`, so the server can be launched automatically from the checked-out plugin source.

## MCP Config

Print a config snippet for your client:

```bash
uvx --from git+https://github.com/binbinsh/agent-labbook@v0.11.0 agent-labbook print-mcp-config
```

Recommended shared `.mcp.json`:

```json
{
  "mcpServers": {
    "labbook": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/binbinsh/agent-labbook@v0.11.0",
        "agent-labbook",
        "mcp"
      ]
    }
  }
}
```

Claude Code project install command:

```bash
claude mcp add labbook --scope project -- uvx --from git+https://github.com/binbinsh/agent-labbook@v0.11.0 agent-labbook mcp
```

If you self-host the Worker backend, add the environment variable in the MCP config:

```json
{
  "mcpServers": {
    "labbook": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/binbinsh/agent-labbook@v0.11.0",
        "agent-labbook",
        "mcp"
      ],
      "env": {
        "AGENT_LABBOOK_BACKEND_URL": "https://labbook.example.com"
      }
    }
  }
}
```

Useful commands:

```bash
uvx --from git+https://github.com/binbinsh/agent-labbook@v0.11.0 agent-labbook doctor
uvx --from git+https://github.com/binbinsh/agent-labbook@v0.11.0 agent-labbook doctor --probe-backend
```

## Typical Flow

1. Run `notion_status`.
2. Run `notion_auth_browser`, or `notion_start_headless_auth` if connect via SSH.
3. Choose the Notion pages or data sources for this project.
4. Run `notion_get_api_context`.
5. Use the returned token, headers, and resource IDs with the official Notion API.

## Hosted Backend

The default backend is `https://labbook.superplanner.net`. It is privacy-friendly: it handles OAuth and token refresh without keeping your project tokens or Notion content in server-side storage. Long-lived tokens and bindings stay local in `.labbook/`.

If you want to self-host it, see [`docs/self-host.md`](./docs/self-host.md).

## Notes

- `.labbook/` should never be committed
- this repo handles auth and project binding, not general Notion API wrapping
- the MCP server implementation uses the official Python SDK rather than a custom wire-protocol implementation
