# Notion API Reference

Compressed reference for direct Notion API use with `agent-labbook`.

## Contents

- Current Baseline
- Latest Breaking Version
- Core Endpoint Map
- Pages, Content, And Properties
- Search Guidance
- Page Creation Rules
- Page Update Rules
- Block Content Rules
- Markdown Endpoints
- Capability Expectations
- Practical Defaults For Agent Labbook

Sources:

- https://developers.notion.com/reference
- https://developers.notion.com/reference/versioning
- https://developers.notion.com/reference/changes-by-version
- https://developers.notion.com/reference/post-search
- https://developers.notion.com/reference/search-optimizations-and-limitations
- https://developers.notion.com/reference/post-page
- https://developers.notion.com/reference/retrieve-a-page
- https://developers.notion.com/reference/retrieve-a-page-property
- https://developers.notion.com/reference/patch-page
- https://developers.notion.com/reference/patch-block-children
- https://developers.notion.com/reference/retrieve-page-markdown
- https://developers.notion.com/guides/data-apis/enhanced-markdown

## Current Baseline

- Base URL: `https://api.notion.com/v1`
- Auth header: `Authorization: Bearer <token>`
- Version header is required: `Notion-Version: 2026-03-11`
- Internal integrations need the target pages or data sources to be shared with the integration.

## Latest Breaking Version

`2026-03-11` is the current API version used by this project.

Key breaking changes since earlier versions:

- `archived` is removed in favor of `in_trash`
- append block children now uses `position`; old `after` is deprecated
- block type `transcription` is renamed to `meeting_notes`
- since `2025-09-03`, data modeling split into `databases` containers and `data_sources` for schema/query operations

Implication for this skill:

- do not assume older `database query` flows are the preferred modern API shape
- when touching block insertion order, use `position`, not `after`
- when trashing or restoring pages/blocks, use `in_trash`

## Core Endpoint Map

Use these endpoints most often:

- Search shared resources: `POST /search`
- Create a page: `POST /pages`
- Retrieve a page object: `GET /pages/{page_id}`
- Retrieve a page property item: `GET /pages/{page_id}/properties/{property_id}`
- Update page properties or metadata: `PATCH /pages/{page_id}`
- Append content blocks: `PATCH /blocks/{block_id}/children`
- Retrieve page content as markdown: `GET /pages/{page_id}/markdown`
- Update page content as markdown: `PATCH /pages/{page_id}/markdown`

## Pages, Content, And Properties

- `GET /pages/{page_id}` returns page properties, not block content.
- To read block content, use block children APIs or markdown retrieval.
- Page property values in a page response are truncated for some reference-heavy property types.
- If a property can exceed 25 references, use the page property item endpoint for the full value.

## Search Guidance

`POST /search` is good for finding pages and data sources shared with the integration.

Use it when:

- the user knows part of a page or data source title
- you need a quick picker or discovery step

Do not rely on it for:

- exhaustive enumeration of everything the bot can access
- querying inside a particular data source
- immediate completeness right after sharing, because indexing can lag

Practical guidance:

- filter by object type when possible
- use a text query when possible
- reduce `page_size` for faster UX
- if a resource must appear quickly, share it directly with the integration

## Page Creation Rules

`POST /pages`

- internal integrations generally need a `page_id` or `data_source` parent
- if parent is a page, only `title` is valid in `properties`
- if parent is a data source, property keys must match the parent schema
- some Notion-managed properties cannot be created manually, including created/edited metadata and rollups
- you can create with `children` or apply a template

## Page Update Rules

`PATCH /pages/{page_id}`

- use it for properties, icon, cover, lock state, trash/restore, and templates
- use `in_trash`, not `archived`
- do not use it to append content blocks
- to add content, call append block children instead
- page parent cannot be changed
- rollup values cannot be updated directly

## Block Content Rules

`PATCH /blocks/{block_id}/children`

- append block children is the standard block-content write path
- pages can be used as the parent block ID
- one request can append at most 100 child blocks
- up to two nesting levels are allowed in a single request
- use `position` to insert at `start`, `end`, or after a specific block

Use this shape for insertion order:

```json
{
  "children": [],
  "position": { "type": "start" }
}
```

Do not use old `after` in new code.

## Markdown Endpoints

Prefer markdown endpoints when your source content already exists as markdown or your agent works natively with markdown.

Use:

- `GET /pages/{page_id}/markdown`
- `PATCH /pages/{page_id}/markdown`

The markdown format is Notion-flavored enhanced markdown, not plain CommonMark. It supports Notion block types, mentions, colors, tables, callouts, synced blocks, and more.

For this skill:

- prefer markdown endpoints for whole-page import/export flows
- prefer block APIs when you need precise structural mutation of part of a page

## Capability Expectations

Common failure pattern: the token is valid but the integration lacks either page access or capability scope.

Check both:

- the page or data source is shared with the integration
- the integration has the required capability such as read content, insert content, or update content

Typical symptoms:

- `404` often means the resource is not shared or not visible to the integration
- `403` often means capability scope is missing

## Practical Defaults For Agent Labbook

- treat `notion_status` and `notion_search_resources` as the preflight checks
- call `notion_get_api_context` only right before direct API use
- prefer exact bound resources over broad workspace search
- prefer markdown endpoints for markdown-native content
- prefer `data_sources` terminology and endpoints in new code
