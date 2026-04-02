---
name: agent-labbook
description: Authorize the hosted Notion public integration, bind project-local resources, and then use the official Notion REST API directly. Use when the agent needs real Notion access through labbook.superplanner.net without a stateful backend.
---

# Agent Labbook

## Purpose

This plugin is only responsible for:

- completing a Notion public integration flow through the hosted Worker at `https://labbook.superplanner.net`
- storing the resulting access token and refresh token only in the local project
- binding one or more Notion pages or data sources to the current project
- returning direct API context such as the access token, headers, and bound resource IDs

It is not a note-taking or task-management wrapper.

## Default Workflow

1. Call `notion_status`.
2. If the project is not authorized yet, call `notion_auth_browser`. If the browser cannot be opened locally, call `notion_start_headless_auth` and later `notion_complete_headless_auth`.
3. If the project still needs more bindings, call `notion_bind_resources`.
4. Call `notion_get_api_context`.
5. Use the official Notion REST API directly with the returned headers and resource IDs.
6. If the exact endpoint is unclear, check the latest official Notion API reference first:
   - `https://developers.notion.com/reference/intro`
   - `https://developers.notion.com/reference/versioning`

## Direct API Rules

- Prefer the official REST API at `https://api.notion.com/v1`.
- Prefer the latest official API docs at `https://developers.notion.com/reference/intro` before guessing an endpoint shape.
- If request or response fields might depend on API versioning, check `https://developers.notion.com/reference/versioning`.
- Treat this plugin as auth and binding infrastructure, not as a content API.
- Use `notion_refresh_session` when a saved token needs to be rotated.
- Reuse aliases from `notion_list_bindings` so later sessions stay consistent.
- Project-local auth state lives under `.agent-labbook/` and should never be committed.

## Common Pattern

1. `notion_status`
2. `notion_auth_browser`
3. `notion_bind_resources`
4. `notion_get_api_context`
5. direct Notion API read or write
