# Versioning Strategy

This repository currently has three independent version boundaries.

## 1. Broker HTTP Contract

- The shared `notion-access-broker` JSON APIs return `api_version`.
- The shared `notion-access-broker` JSON APIs also return `supported_api_versions`.
- `agent-labbook` validates that value against its local `BROKER_API_VERSION`.
- `agent-labbook` sends `X-Notion-Access-Broker-Accept-Api-Versions` on broker JSON requests so incompatible upgrades fail before a flow continues.
- A mismatched broker API version is treated as a hard compatibility error.

Why:

- browser flows and local MCP flows both depend on the broker
- silent field drift is harder to debug than an explicit version error

When changing the broker API:

1. bump the broker contract docs and snapshot
2. bump `BROKER_API_VERSION` in clients that consume the new contract
3. keep old clients pinned to the previous broker deployment, or add an explicit compatibility path

## 2. `.labbook` Project State

The local state files are independently versioned:

- `session.json`
- `bindings.json`
- `pending-auth.json`
- `pending-handoff.json`
- `local-handoff-server.json`

Current strategy:

- current schema version for each file is `1`
- load paths accept legacy versionless payloads and normalize them to version `1`
- session and pending-auth state also inject the current integration id when it is missing
- session and pending-auth state reject payloads that claim a different integration id
- future versions are rejected explicitly

Why:

- older local state written before schema versioning should keep working
- newer state should not be read incorrectly by older binaries

When changing a local state schema:

1. bump the corresponding version constant in `src/labbook/state.py`
2. add a migration path for older payloads in `_normalize_state_payload`
3. add tests for both the legacy and future-version cases

## 3. Shared Credential Index

The shared credential index stored by `notion_access_broker.credentials` also has its own version.

Current strategy:

- current `INDEX_VERSION` is `1`
- versionless stored indexes are normalized to version `1`
- unsupported future versions are rejected explicitly

When changing the index format:

1. bump `INDEX_VERSION`
2. add migration logic in `_parse_index_payload`
3. add tests for versionless, current, and future-version payloads

## Upgrade Philosophy

The default rule is:

- migrate old known formats deliberately
- reject unknown future formats loudly

That keeps the current release easy to reason about, while still giving us a clean place to add real migrations later.
