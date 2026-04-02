# agent-labbook

Bind a Codex or Claude Code project to selected Notion pages and data sources for direct agent access through the official Notion API.

The point of this repo is simple: let an agent use real Notion access in a project without turning this project into yet another Notion SDK.

It handles the part that is usually annoying:

- starting the official Notion authorization flow
- letting you choose which Notion pages or data sources belong to this project
- saving the auth session and bindings inside the local project
- giving the agent the real Notion API token, headers, and bound resource IDs

After that, the agent can call the official Notion REST API directly.

## What This Is For

Use this when you want an agent to work with Notion from inside a repo or project folder, for example:

- read a product spec, roadmap, or notes page from Notion
- write updates back to a Notion page or data source
- keep each repo connected to its own Notion resources
- avoid building a custom Notion wrapper just to get auth working

This project is intentionally not a full Notion SDK and not a full CRUD wrapper for every Notion endpoint. It is the connection layer that gets your agent into Notion cleanly, then gets out of the way.

## What The Agent Can Do With It

Once installed, the agent gets MCP tools for the whole project lifecycle:

- check whether this project is already connected to Notion
- start a normal browser auth flow
- start a headless auth flow if the browser is on another machine or cannot be opened locally
- choose and save the pages or data sources that should belong to this project
- bind more resources later from a Notion URL or resource ID
- list the resources already bound to the project
- refresh the saved session
- clear the saved auth if you want to disconnect the project
- return API context for direct Notion API calls

Project state is stored locally in `.agent-labbook/`, so the saved tokens and bindings stay with the current project instead of being shared globally across every repo.

## Install

The easiest install path is to give this repository URL to your coding agent:

- `https://github.com/binbinsh/agent-labbook`

### Codex

Tell Codex to install or add this repository as a plugin/MCP repo:

- `https://github.com/binbinsh/agent-labbook`

This repo already includes the metadata and MCP config Codex needs.

### Claude Code

Tell Claude Code to use this repository as the MCP server source for the project:

- `https://github.com/binbinsh/agent-labbook`

If you need manual setup, the server command is already defined in [`.mcp.json`](./.mcp.json) and starts [`scripts/run_mcp_server.py`](./scripts/run_mcp_server.py).

### Requirements

- Python 3.10 or newer
- a Codex, Claude Code, or other MCP-capable environment that can run a local MCP server

## Typical Flow

1. Ask the agent to run `notion_status`.
2. If the project is not connected yet, run `notion_auth_browser`.
3. If a local browser is not available, use `notion_start_headless_auth` and later `notion_complete_headless_auth`.
4. In the browser, choose which Notion pages or data sources should be bound to this project.
5. Run `notion_get_api_context`.
6. Use the returned token, headers, and resource IDs with the official Notion API.

## Main MCP Tools

- `notion_status`: check whether the current project already has a saved Notion session and bindings
- `notion_auth_browser`: start the browser-based authorization flow
- `notion_start_headless_auth`: create an auth link for a headless or remote-browser flow
- `notion_complete_headless_auth`: finish the headless flow with the returned handoff bundle
- `notion_bind_resources`: bind more Notion pages or data sources to the project
- `notion_list_bindings`: list the resources currently bound to the project
- `notion_get_api_context`: return the saved access token, API headers, and bound resource IDs
- `notion_refresh_session`: refresh the saved Notion session
- `notion_clear_project_auth`: remove the saved session, and optionally the bindings too
- `notion_setup_guide`: return the short setup guide from inside the MCP server

## Default Backend

By default, the auth flow goes through the hosted backend at `https://labbook.superplanner.net`.

Most users can just use that default. If you want to self-host it on your own Cloudflare account and domain, there is a short guide in [`docs/self-host.md`](./docs/self-host.md).

## Notes

- `.agent-labbook/` should never be committed
- this repo helps the agent get authorized and project-scoped; it does not replace the official Notion API
- after auth, the intended path is: get API context here, then call Notion directly
