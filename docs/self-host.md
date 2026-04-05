# Self-Hosting Agent Labbook

This repository ships with a default hosted app backend at `https://superplanner.ai/notion/agent-labbook` and a shared OAuth backend at `https://superplanner.ai/notion/oauth`, which together keep long-lived tokens out of server-side storage. You can self-host the Agent Labbook app Worker on your own Cloudflare account and pair it with the separate `notion-access-broker` Worker.

## Recommended architecture

In the shared architecture:

1. `notion-access-broker` owns `/notion/oauth/start`, `/callback`, `/api/refresh`, and signed handoff APIs
2. `agent-labbook` owns `/notion/agent-labbook/...` and renders the selection UI
3. the local MCP server exposes read-only project context through MCP resources, runs mutating auth and binding steps through MCP tools, stores project bindings under `.labbook/`, and stores long-lived tokens through the shared `notion-access-broker` credential provider helpers

## MCP surface

The local MCP server now separates the surface by responsibility:

- resources for read-only project context:
  - `labbook://agent-labbook/setup-guide`
  - `labbook://agent-labbook/project/status`
  - `labbook://agent-labbook/project/bindings`
- prompts for workflow guidance:
  - `notion_connect_project`
  - `notion_use_bound_resources`
- tools for side effects such as starting auth, finalizing a pending handoff, binding resources, or clearing state
- tools also advertise `outputSchema`, so clients can rely on stable structured results and the MCP low-level server validates successful tool payloads against those schemas

In particular, `notion_status` is read-only. After a browser flow finishes, the model should call `notion_status` again and then call `notion_finalize_pending_auth` only if `pending_handoff_ready=true`.

For versioning and upgrade boundaries across the broker API, local `.labbook` files, and the shared credential index, see [`versioning.md`](./versioning.md).

## What stays local

The following stay on the user's machine under `.labbook/`:

- selected resource bindings
- pending local auth metadata

The following stay on the user's machine outside Cloudflare Worker storage:

- the long-lived access token and refresh token
- the selected shared credential provider metadata

By default, the local credential provider prefers 1Password when the local `op` CLI is available and can access 1Password.

If `NOTION_ACCESS_BROKER_1PASSWORD_VAULT=YOUR_VAULT` is set, Agent Labbook pins 1Password to that vault. If it is unset, 1Password uses the account's default vault.

If 1Password is unavailable, it falls back to the system keyring. You can still force either provider explicitly with:

- `NOTION_ACCESS_BROKER_CREDENTIAL_PROVIDER=1password`
- `NOTION_ACCESS_BROKER_CREDENTIAL_PROVIDER=keyring`

With that setup, each integration only needs one OAuth authorization per machine and credential provider. New projects can attach the saved shared credential reference locally, reopen the browser-based selection UI if needed, and then bind their own project-specific pages or data sources.

The Worker handles tokens in memory during requests, but does not persist them after the request completes.

For the browser-based handoff, the browser also temporarily receives the token payload so it can return it to the local project or package it into the headless handoff bundle. Treat the handoff page and copied bundle as sensitive until the flow is complete.

The generated handoff bundle is signed by the Worker and validated by the Worker again before the local project accepts it. If you fork this design, keep that verification step in place.

## Requirements

- a Cloudflare account with access to the target zone
- either a dedicated Worker hostname such as `labbook.example.com`, or a routed path prefix such as `example.com/notion/agent-labbook`
- a Notion Public integration
- Node.js and Wrangler

## 1. Deploy or reuse notion-access-broker

Deploy the sibling `notion-access-broker` Worker and note its public base URL, for example:

- `https://example.com/notion/oauth`

In the Notion developer portal:

1. Create a Public integration.
2. Set the redirect URI to `YOUR_OAUTH_BASE_URL/callback`.
3. Copy the client ID and client secret.

## 2. Update the Agent Labbook app domain

Edit [`wrangler.toml`](../wrangler.toml):

- set `PUBLIC_BASE_URL` to your final base URL
- set `NOTION_OAUTH_BASE_URL` to your shared OAuth Worker base URL
- choose either a custom domain route for a dedicated hostname, or zone routes for a path prefix such as `https://example.com/notion/agent-labbook`

Example:

```toml
[vars]
PUBLIC_BASE_URL = "https://example.com/notion/agent-labbook"
NOTION_OAUTH_BASE_URL = "https://example.com/notion/oauth"

[[routes]]
pattern = "example.com/notion/agent-labbook"
zone_name = "example.com"

[[routes]]
pattern = "example.com/notion/agent-labbook/*"
zone_name = "example.com"
```

## 3. Install dependencies and log in

```bash
npm install
npx wrangler login
```

## 4. Set Worker secrets

If you are using the shared `notion-access-broker` Worker, the Agent Labbook app Worker does not need Notion client secrets. Configure the Notion client credentials on the `notion-access-broker` Worker instead.

## 5. Deploy

```bash
npm run worker:deploy
```

## 6. Verify

```bash
curl YOUR_BASE_URL/health
```

The response should show:

- `ok: true`
- `oauth_base_url: https://example.com/notion/oauth`
- `continue_url: https://example.com/notion/agent-labbook/oauth/continue`

## 7. Point the MCP server at your Worker

Set this environment variable in the MCP server config:

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
        "AGENT_LABBOOK_BACKEND_URL": "YOUR_BASE_URL",
        "AGENT_LABBOOK_OAUTH_BASE_URL": "YOUR_OAUTH_BASE_URL"
      }
    }
  }
}
```

Then Agent Labbook will use your app Worker instead of `https://superplanner.ai/notion/agent-labbook` and your OAuth Worker instead of `https://superplanner.ai/notion/oauth`.

Use this snippet in your client's MCP config, or pass the same command and environment through `codex mcp add` / `claude mcp add`.

You can verify the local setup with:

```bash
uvx agent-labbook doctor --probe-backend
```

## 8. Recommended validation flow

After wiring the MCP server to your self-hosted Workers, validate the workflow in this order:

1. Read `labbook://agent-labbook/project/status` or call `notion_status`.
2. If reusable shared credentials already exist, prefer `notion_list_saved_credentials` and `notion_attach_saved_credential`.
3. Otherwise start a browser or headless auth flow.
4. After the browser says the project is connected, call `notion_status` again.
5. If `pending_handoff_ready=true`, call `notion_finalize_pending_auth`.
6. Read `labbook://agent-labbook/project/bindings` or call `notion_list_bindings`.
7. Call `notion_get_api_context` only when you are ready to use the official Notion API.
