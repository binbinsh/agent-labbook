# agent-labbook

`agent-labbook` is a Codex-first MCP plugin repository that gives an agent real Notion access without turning this repository into a Notion SDK.

Its job is intentionally narrow:

- it starts the official Notion Public integration OAuth flow
- it completes OAuth through a very small Cloudflare Worker backend
- it lets the user choose which pages or data sources belong to the current project
- it stores tokens and bindings only in the local project
- it returns direct API context so the agent can call the official Notion REST API itself

This repository is not a standalone CLI, not a Python package meant for end users to install directly, and not a generic wrapper around every Notion API endpoint.

It ships with Codex plugin metadata out of the box, and the same MCP server can also be wired into other MCP-capable clients manually.

## What It Is For

Use this when you want Codex to:

- connect a repo or workspace to Notion
- keep the authorized scope tied to project-local bindings
- reuse the real Notion REST API instead of a custom abstraction layer
- avoid keeping user tokens in a long-lived backend database

The default hosted backend is:

- `https://labbook.superplanner.net`

That backend only handles OAuth exchange, refresh, and the browser-based resource selection handoff.

## How It Works

The architecture is split cleanly:

- the MCP plugin runs locally in Codex
- the Cloudflare Worker handles the Notion OAuth redirect flow
- the user chooses accessible Notion resources in the browser
- the Worker sends the selected result back to the local project or generates a handoff bundle
- the project stores `access_token`, `refresh_token`, and bindings under `.agent-labbook/`

No KV, D1, R2, Durable Objects, or per-user backend storage are required.

## Install In Codex

The easiest install path is to give Codex this repository URL:

- `https://github.com/binbinsh/agent-labbook`

This repo already includes:

- [`plugin.json`](./.codex-plugin/plugin.json)
- [`.mcp.json`](./.mcp.json)
- [`scripts/run_mcp_server.py`](./scripts/run_mcp_server.py)

So Codex can install it directly from GitHub as a plugin repository.

After install, the agent should use these tools:

- `notion_status`
- `notion_setup_guide`
- `notion_auth_browser`
- `notion_start_headless_auth`
- `notion_complete_headless_auth`
- `notion_refresh_session`
- `notion_clear_project_auth`
- `notion_bind_resources`
- `notion_list_bindings`
- `notion_get_api_context`

## Typical User Flow

1. Call `notion_status`.
2. If the project is not authorized yet, call `notion_auth_browser`.
3. If a local browser is not available, call `notion_start_headless_auth`, finish the flow in any browser, and then call `notion_complete_headless_auth`.
4. Choose which pages or data sources should be bound to the project on the Worker handoff page. When a selected root page has discoverable child pages, the Worker can include them in the final bundle automatically.
5. Call `notion_get_api_context`.
6. Use the returned headers and resource IDs with the official Notion REST API.

## Requirements

For normal plugin use:

- Python 3.10 or newer
- a Codex environment that can run a local MCP server

For self-hosting the Worker:

- Node.js 20 or newer
- Wrangler and a Cloudflare account
- a Notion Public integration

## Local State

Project-local files:

- `<project>/.agent-labbook/session.json`
- `<project>/.agent-labbook/bindings.json`
- `<project>/.agent-labbook/pending-auth.json`

`session.json` contains:

- `access_token`
- `refresh_token`
- `workspace_id`
- `workspace_name`
- `bot_id`

`bindings.json` contains:

- `default_resource_alias`
- `resources[]`

Each bound resource entry contains:

- `resource_id`
- `resource_type`
- `resource_url`
- `alias`
- `title`
- `bound_at`
- `source`

`.gitignore` already excludes `.agent-labbook/` because those files are sensitive.

## Deploy The Hosted Worker

The Worker config is already checked in at [`wrangler.toml`](./wrangler.toml) and defaults to:

- `https://labbook.superplanner.net`

### 1. Create the Notion public integration

In the Notion developer portal:

1. Create a Public integration named `Agent Labbook`.
2. Add the redirect URI `https://labbook.superplanner.net/oauth/callback`.
3. Keep the generated client ID and client secret.

### 2. Install dependencies

```bash
npm install
```

### 3. Log into Cloudflare

```bash
npx wrangler login
```

### 4. Put production secrets into the Worker

For deployed Workers, yes: the final place for the production Notion secrets is the Worker secret store via `wrangler secret put`.

```bash
npx wrangler secret put NOTION_CLIENT_ID
npx wrangler secret put NOTION_CLIENT_SECRET
```

`.env` is useful as a local staging source for deployment automation, but the deployed Worker should read secrets from Cloudflare's secret store.

### 5. Deploy

```bash
npm run worker:deploy
```

### 6. Verify

```bash
curl https://labbook.superplanner.net/health
```

The response should include:

- `ok: true`
- `configured: true`
- `redirect_uri: https://labbook.superplanner.net/oauth/callback`

## Self-Hosting

If someone wants to host their own Worker and domain, the full guide lives here:

- [`docs/self-host.md`](./docs/self-host.md)

For self-hosting, they should:

- deploy the Worker to their own Cloudflare account
- set their own custom domain and redirect URI
- put `NOTION_CLIENT_ID` and `NOTION_CLIENT_SECRET` into that Worker
- point the plugin at it with `AGENT_LABBOOK_BACKEND_URL`

## Privacy Model

The Worker is intentionally thin:

- it does not persist user tokens in Cloudflare storage
- it does not persist Notion content in Cloudflare storage
- it only sees OAuth codes and tokens in memory while processing each request
- long-lived tokens remain in the user's local project, not in a server-side database

For the stateless browser handoff to work, the browser page also receives the token payload long enough to hand it back to the local project or to generate the headless handoff bundle. That means the browser session and copied handoff bundle should be treated as sensitive until the auth flow is finished.

To keep the Worker stateless while still protecting the OAuth `state` parameter, the Worker signs the state payload using the existing `NOTION_CLIENT_SECRET`. That removes the need for a separate `WORKER_STATE_SECRET`.

## Resource Discovery Limits

The chooser page is built from Notion's `search` API. In practice that means:

- newly shared resources can take a little time to appear
- the initial list may need `Refresh` once or twice after OAuth
- the chooser is a discovery surface, not a guaranteed full workspace inventory

If something is still missing, you can add it later with `notion_bind_resources` by passing a Notion page URL or resource ID directly.

## What The Plugin Does Not Do

This repository deliberately does not implement a full Notion CRUD wrapper, note sync layer, or task abstraction. Once the plugin returns API context, the agent should call the original Notion API directly.

## Files

The plugin entrypoints are:

- [`.codex-plugin/plugin.json`](./.codex-plugin/plugin.json)
- [`.mcp.json`](./.mcp.json)
- [`skills/labbook/SKILL.md`](./skills/labbook/SKILL.md)
- [`scripts/run_mcp_server.py`](./scripts/run_mcp_server.py)

The MCP server implementation lives in:

- [`src/agent_labbook/mcp_server.py`](./src/agent_labbook/mcp_server.py)
- [`src/agent_labbook/service.py`](./src/agent_labbook/service.py)
- [`src/agent_labbook/notion_api.py`](./src/agent_labbook/notion_api.py)
- [`src/agent_labbook/state.py`](./src/agent_labbook/state.py)

The Cloudflare Worker lives in:

- [`worker/src/index.js`](./worker/src/index.js)
- [`docs/self-host.md`](./docs/self-host.md)
