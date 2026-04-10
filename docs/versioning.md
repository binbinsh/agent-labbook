# Versioning Strategy

This repository now has two local version boundaries.

## 1. `.labbook` Project State

The local state files are independently versioned:

- `session.json`
- `bindings.json`

Current strategy:

- current `session.json` schema version is `3`
- current `bindings.json` schema version is `1`
- load paths accept versionless payloads and normalize them to the current version
- `session.json` stores metadata such as `storage`, `op_ref`, workspace info, and bot info, but not the secret itself
- unsupported future versions are rejected explicitly

When changing a local state schema:

1. bump the corresponding version constant in [src/labbook/state.py](../src/labbook/state.py)
2. update `_normalize_state_payload`
3. add tests for current and future-version cases

## 2. MCP Surface

The MCP tool, prompt, and resource names are now part of the compatibility surface.

Current strategy:

- additive changes are preferred
- breaking renames should be paired with explicit release notes
- tool output schemas should continue to match the server implementation

When changing the MCP surface:

1. update the tool and resource definitions in [src/labbook/mcp_server.py](../src/labbook/mcp_server.py)
2. update the related tests in [tests/test_mcp_server.py](../tests/test_mcp_server.py)
3. document the user-visible change in the README or release notes

## 0.17.1 Migration Note

Version 0.17.1 is a clean architectural break. It removes the OAuth/Cloudflare Worker model entirely and replaces it with direct Notion Internal Integration secret management. As a result:

- `session.json` files from versions prior to 0.17.1 (schema versions 1 and 2) are intentionally rejected on load.
- There is no automated migration path from earlier schema versions.
- Users upgrading from 0.13.x or 0.14.x should delete their existing `.labbook/` directory and reconfigure with `notion_configure_internal_integration`.

This is intentional. The old session schema stored OAuth-specific fields that no longer apply.

## Upgrade Philosophy

The default rule is:

- keep the current state format explicit
- reject unknown future formats loudly

That keeps the local project state predictable and avoids carrying long-lived compatibility branches inside the server.
