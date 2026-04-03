# Self-Hosting Agent Labbook

This repository ships with a default hosted backend at `https://labbook.superplanner.net`, which is privacy-friendly and keeps long-lived tokens out of server-side storage. You can also self-host the Worker on your own Cloudflare account and domain.

## What you are hosting

The Cloudflare Worker does only three things:

1. starts the Notion public OAuth flow
2. exchanges auth codes and refresh tokens with Notion
3. renders the resource selection page that hands chosen bindings back to the local project

It does not require KV, D1, R2, Durable Objects, or any user database.

## What stays local

The following stay on the user's machine under `.labbook/`:

- `access_token`
- `refresh_token`
- selected resource bindings
- pending local auth metadata

The Worker handles tokens in memory during requests, but does not persist them after the request completes.

For the browser-based handoff, the browser also temporarily receives the token payload so it can return it to the local project or package it into the headless handoff bundle. Treat the handoff page and copied bundle as sensitive until the flow is complete.

The generated handoff bundle is signed by the Worker and validated by the Worker again before the local project accepts it. If you fork this design, keep that verification step in place.

## Requirements

- a Cloudflare account with access to the target zone
- a DNS name for the Worker, such as `labbook.example.com`
- a Notion Public integration
- Node.js and Wrangler

## 1. Create the Notion public integration

In the Notion developer portal:

1. Create a Public integration.
2. Set the redirect URI to `https://YOUR_DOMAIN/oauth/callback`.
3. Copy the client ID and client secret.

## 2. Update the Worker domain

Edit [`wrangler.toml`](../wrangler.toml):

- set `PUBLIC_BASE_URL` to your final domain
- set `routes[[0]].pattern` to that domain

Example:

```toml
[vars]
PUBLIC_BASE_URL = "https://labbook.example.com"

[[routes]]
pattern = "labbook.example.com"
custom_domain = true
```

## 3. Install dependencies and log in

```bash
npm install
npx wrangler login
```

## 4. Set Worker secrets

For production, put secrets into the Worker with Wrangler:

```bash
npx wrangler secret put NOTION_CLIENT_ID
npx wrangler secret put NOTION_CLIENT_SECRET
```

These are the only required Worker secrets.

## 5. Deploy

```bash
npm run worker:deploy
```

## 6. Verify

```bash
curl https://YOUR_DOMAIN/health
```

The response should show:

- `ok: true`
- `configured: true`
- `redirect_uri: https://YOUR_DOMAIN/oauth/callback`

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
        "AGENT_LABBOOK_BACKEND_URL": "https://YOUR_DOMAIN"
      }
    }
  }
}
```

Then Agent Labbook will use your Worker instead of `https://labbook.superplanner.net`.

Use this snippet in your client's MCP config, or pass the same command and environment through `codex mcp add` / `claude mcp add`.

You can verify the local setup with:

```bash
uvx agent-labbook doctor --probe-backend
```

## 8. Understand the chooser limits

The browser chooser is based on Notion's `search` API rather than a true "list every accessible resource" endpoint. That means a fresh OAuth session may need `Refresh` before every shared page or data source appears.

If a specific page or data source still does not show up, bind it later from the MCP side with `notion_bind_resources` using its Notion URL or resource ID.
