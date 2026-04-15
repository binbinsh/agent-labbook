from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .binding_discovery import (
    discover_children_for_resource,
    normalize_notion_resource,
)
from .notion_api import NOTION_API_BASE, NotionClient
from .state import (
    LabbookError,
    load_project_bindings,
    normalize_notion_id,
    resolve_project_root,
    save_project_bindings,
)

__all__ = [
    "bind_resource_urls",
    "bind_resources",
    "build_bind_resource_urls_payload",
    "build_bind_resources_payload",
    "build_discover_children_payload",
    "build_list_bindings_payload",
    "build_search_resources_payload",
    "discover_children",
    "list_bindings",
    "project_bindings_resource_text",
    "search_resources",
]

# ---------------------------------------------------------------------------
# Pagination / limit constants
# ---------------------------------------------------------------------------

DEFAULT_SEARCH_PAGE_SIZE = 25
MIN_SEARCH_PAGE_SIZE = 1
MAX_SEARCH_PAGE_SIZE = 100

DEFAULT_DISCOVERY_LIMIT = 50
MIN_DISCOVERY_LIMIT = 1
MAX_DISCOVERY_LIMIT = 200

_MAX_ALIAS_SUFFIX = 10_000

_VALID_RESOURCE_TYPES = {"page", "data_source"}
_VALID_SELECTION_SCOPES = {"resource", "subtree"}


# ---------------------------------------------------------------------------
# Small normalization helpers
# ---------------------------------------------------------------------------


def _coerce_resource_type(raw: str | None) -> str | None:
    """Normalize a resource type string, coercing ``database`` to ``data_source``.

    Returns ``None`` when the input is empty.  Raises :class:`LabbookError`
    when the value is non-empty but not a recognized type.
    """
    clean = str(raw or "").strip().lower()
    if not clean:
        return None
    if clean == "database":
        clean = "data_source"
    if clean not in _VALID_RESOURCE_TYPES:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    return clean


def _normalize_selection_scope(value: Any) -> str:
    """Return ``'resource'`` or ``'subtree'``; raise on anything else."""
    clean = str(value or "resource").strip().lower()
    if clean not in _VALID_SELECTION_SCOPES:
        raise LabbookError("selection_scope must be 'resource' or 'subtree'.")
    return clean


def normalize_search_page_size(page_size: int | str | None = None) -> int:
    """Clamp *page_size* into ``[MIN, MAX]``, defaulting to ``DEFAULT``."""
    if page_size in (None, ""):
        return DEFAULT_SEARCH_PAGE_SIZE
    try:
        limit = int(page_size)
    except (TypeError, ValueError) as exc:
        raise LabbookError("page_size must be an integer number of results.") from exc
    return min(max(limit, MIN_SEARCH_PAGE_SIZE), MAX_SEARCH_PAGE_SIZE)


def normalize_discovery_limit(limit: int | str | None = None) -> int:
    """Clamp *limit* into ``[MIN, MAX]``, defaulting to ``DEFAULT``."""
    if limit in (None, ""):
        return DEFAULT_DISCOVERY_LIMIT
    try:
        parsed = int(limit)
    except (TypeError, ValueError) as exc:
        raise LabbookError("limit must be an integer number of results.") from exc
    return min(max(parsed, MIN_DISCOVERY_LIMIT), MAX_DISCOVERY_LIMIT)


def _slugify_alias(text: str, *, fallback: str) -> str:
    """Convert *text* to a URL-safe slug, falling back when empty."""
    candidate = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return candidate or fallback


def _endpoint_for_resource(resource_type: str | None, resource_id: str) -> str | None:
    """Return the Notion API endpoint for a known resource type, or ``None``."""
    clean_id = normalize_notion_id(resource_id)
    endpoints = {
        "page": f"{NOTION_API_BASE}/pages/{clean_id}",
        "data_source": f"{NOTION_API_BASE}/data_sources/{clean_id}",
    }
    return endpoints.get(resource_type or "")


# ---------------------------------------------------------------------------
# Binding entry helpers
# ---------------------------------------------------------------------------


def _normalize_binding_entry(
    *,
    resource_id: str,
    resource_type: str,
    resource_url: str | None,
    title: str | None,
    alias: str | None,
    selection_scope: str | None = None,
    source: str = "manual_bind",
    bound_at: str | None = None,
) -> dict[str, Any]:
    """Build a canonical binding dict from raw field values."""
    clean_id = normalize_notion_id(resource_id)
    clean_type = _coerce_resource_type(resource_type)
    if clean_type is None:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    clean_title = str(title or clean_id).strip() or clean_id
    clean_alias = _slugify_alias(alias or clean_title, fallback=clean_type)
    clean_url = str(resource_url or "").strip() or _endpoint_for_resource(
        clean_type, clean_id
    )
    return {
        "alias": clean_alias,
        "resource_id": clean_id,
        "resource_type": clean_type,
        "resource_url": clean_url,
        "title": clean_title,
        "source": source,
        "bound_at": bound_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "selection_scope": _normalize_selection_scope(selection_scope),
    }


def _normalize_resource_input(item: dict[str, Any]) -> dict[str, str | None]:
    """Normalize a single ``resource_refs`` item from user input."""
    resource_ref = str(
        item.get("resource_id_or_url")
        or item.get("resource_id")
        or item.get("resource_url")
        or ""
    ).strip()
    if not resource_ref:
        raise LabbookError(
            "Each resource_refs item must include "
            "resource_id_or_url, resource_id, or resource_url."
        )
    return {
        "resource_id": normalize_notion_id(resource_ref),
        "resource_type": _coerce_resource_type(item.get("resource_type")),
        "resource_url": str(item.get("resource_url") or "").strip() or None,
        "alias": str(item.get("alias") or "").strip() or None,
        "title": str(item.get("title") or "").strip() or None,
        "selection_scope": _normalize_selection_scope(item.get("selection_scope")),
    }


# ---------------------------------------------------------------------------
# Alias deduplication
# ---------------------------------------------------------------------------


def _alias_for_resource(
    *,
    resource_id: str,
    proposed_alias: str,
    used_aliases: set[str],
    alias_owner: dict[str, str],
) -> str:
    """Return a unique alias, appending a numeric suffix on collision."""
    if (
        proposed_alias not in used_aliases
        or alias_owner.get(proposed_alias) == resource_id
    ):
        used_aliases.add(proposed_alias)
        alias_owner[proposed_alias] = resource_id
        return proposed_alias

    for suffix in range(2, _MAX_ALIAS_SUFFIX + 1):
        candidate = f"{proposed_alias}-{suffix}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            alias_owner[candidate] = resource_id
            return candidate

    raise RuntimeError(
        f"Could not find a unique alias for {proposed_alias!r} "
        f"after {_MAX_ALIAS_SUFFIX} attempts"
    )


# ---------------------------------------------------------------------------
# Binding list helpers
# ---------------------------------------------------------------------------


def _existing_bindings(project_root: Path) -> list[dict[str, Any]]:
    """Load the current binding list for a project (may be empty)."""
    payload = load_project_bindings(project_root) or {}
    resources = payload.get("resources")
    if not isinstance(resources, list):
        return []
    return [item for item in resources if isinstance(item, dict)]


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    """When exactly one binding exists, return its alias."""
    if len(resources) != 1:
        return None
    return str(resources[0].get("alias") or "").strip() or None


def _sorted_bindings(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort bindings by title, type, then id for stable ordering."""
    return sorted(
        resources,
        key=lambda item: (
            str(item.get("title") or "").lower(),
            str(item.get("resource_type") or "").lower(),
            str(item.get("resource_id") or "").lower(),
        ),
    )


def _bindings_payload(
    project_root: Path, resources: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the canonical bindings payload for persistence."""
    return {
        "project_root": str(project_root),
        "default_resource_alias": _default_resource_alias(resources),
        "resources": _sorted_bindings(resources),
    }


# ---------------------------------------------------------------------------
# Search ranking
# ---------------------------------------------------------------------------


def _rank_search_results(
    results: list[dict[str, Any]], *, query: str | None = None
) -> list[dict[str, Any]]:
    """Rank search results: exact > prefix > contains; workspace roots first."""
    clean_query = str(query or "").strip().lower()
    if not clean_query:
        return results

    def _match_rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
        title = str(item.get("title") or "").strip().lower()
        parent = item.get("parent")
        parent_type = (
            str(parent.get("type") or "").strip().lower()
            if isinstance(parent, dict)
            else ""
        )
        resource_type = str(item.get("resource_type") or "").strip().lower()

        if title == clean_query:
            title_rank = 0
        elif title.startswith(clean_query):
            title_rank = 1
        elif clean_query in title:
            title_rank = 2
        else:
            title_rank = 3

        root_rank = 0 if parent_type == "workspace" else 1
        type_rank = {"page": 0, "data_source": 1}.get(resource_type, 2)
        return (title_rank, root_rank, type_rank, title)

    return sorted(results, key=_match_rank)


# ---------------------------------------------------------------------------
# Payload builders (used by both the UI layer and the MCP tools)
# ---------------------------------------------------------------------------


def build_search_resources_payload(
    client: NotionClient,
    *,
    project_root: str,
    query: str | None = None,
    page_size: int | str | None = None,
) -> dict[str, Any]:
    """Search the Notion workspace and return a ranked result payload."""
    page_limit = normalize_search_page_size(page_size)
    raw = client.search(query=query, page_size=page_limit)
    results: list[dict[str, Any]] = []
    for item in raw.get("results", []):
        if not isinstance(item, dict):
            continue
        normalized = normalize_notion_resource(item)
        if normalized is not None:
            results.append(normalized)
    results = _rank_search_results(results, query=query)
    return {
        "project_root": str(project_root),
        "query": str(query or "").strip() or None,
        "page_size": page_limit,
        "result_count": len(results),
        "results": results,
    }


def build_discover_children_payload(
    client: NotionClient,
    *,
    project_root: str,
    resource_id_or_url: str,
    resource_type: str | None = None,
    limit: int | str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Discover children of a Notion resource and return as payload."""
    page_limit = normalize_discovery_limit(limit)
    root_resource = client.retrieve_resource(resource_id_or_url, resource_type)
    payload = discover_children_for_resource(
        client,
        root_resource,
        page_limit=page_limit,
        mode=str(mode or "shallow"),
    )
    payload["project_root"] = str(project_root)
    return payload


def build_bind_resources_payload(
    client: NotionClient,
    *,
    project_root: str | Path,
    resource_refs: list[dict[str, Any]],
    default_alias: str | None = None,
) -> dict[str, Any]:
    """Resolve, validate, and persist a set of resource bindings."""
    if not resource_refs:
        raise LabbookError("resource_refs must contain at least one resource.")

    root = resolve_project_root(project_root)
    existing_resources = _existing_bindings(root)
    by_resource_id: dict[str, dict[str, Any]] = {
        str(item.get("resource_id")): dict(item)
        for item in existing_resources
        if isinstance(item.get("resource_id"), str)
    }

    for index, raw_item in enumerate(resource_refs):
        if not isinstance(raw_item, dict):
            raise LabbookError("Each item in resource_refs must be an object.")
        normalized_input = _normalize_resource_input(raw_item)
        resource = client.retrieve_resource(
            normalized_input["resource_id"] or "",
            normalized_input["resource_type"],
        )
        normalized_resource = normalize_notion_resource(resource)
        if normalized_resource is None:
            raise LabbookError(
                f"Resource {normalized_input['resource_id']} "
                "is not a page or data source."
            )

        fallback_alias = (
            default_alias if len(resource_refs) == 1 and default_alias else None
        )
        previous = by_resource_id.get(normalized_resource["resource_id"])
        by_resource_id[normalized_resource["resource_id"]] = _normalize_binding_entry(
            resource_id=normalized_resource["resource_id"],
            resource_type=normalized_input["resource_type"]
            or normalized_resource["resource_type"],
            resource_url=normalized_input["resource_url"]
            or normalized_resource["resource_url"],
            title=normalized_input["title"] or normalized_resource["title"],
            alias=normalized_input["alias"]
            or fallback_alias
            or (previous or {}).get("alias")
            or f"resource-{index + 1}",
            selection_scope=normalized_input["selection_scope"],
            source="manual_bind",
            bound_at=(previous or {}).get("bound_at"),
        )

    final_resources = _sorted_bindings(list(by_resource_id.values()))

    # Deduplicate aliases across the full set.
    used_aliases: set[str] = set()
    alias_owner: dict[str, str] = {}
    for resource in final_resources:
        resource["alias"] = _alias_for_resource(
            resource_id=str(resource["resource_id"]),
            proposed_alias=_slugify_alias(
                str(resource["alias"]), fallback=str(resource["resource_type"])
            ),
            used_aliases=used_aliases,
            alias_owner=alias_owner,
        )

    payload = _bindings_payload(root, final_resources)
    save_project_bindings(root, payload)
    return {
        "project_root": str(root),
        "default_resource_alias": payload["default_resource_alias"],
        "resource_count": len(final_resources),
        "resources": final_resources,
    }


def build_bind_resource_urls_payload(
    client: NotionClient,
    *,
    project_root: str | Path,
    resource_urls: list[str],
    selection_scope: str | None = "subtree",
    default_alias: str | None = None,
) -> dict[str, Any]:
    """Bind resources by Notion URL, delegating to :func:`build_bind_resources_payload`."""
    normalized_urls = [
        str(item).strip() for item in resource_urls if str(item or "").strip()
    ]
    if not normalized_urls:
        raise LabbookError("resource_urls must contain at least one Notion URL.")

    return build_bind_resources_payload(
        client,
        project_root=project_root,
        resource_refs=[
            {
                "resource_id_or_url": url,
                "selection_scope": _normalize_selection_scope(selection_scope),
            }
            for url in normalized_urls
        ],
        default_alias=default_alias,
    )


def build_list_bindings_payload(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return the current bindings for a project (no Notion API call)."""
    root = resolve_project_root(project_root)
    payload = load_project_bindings(root) or _bindings_payload(root, [])
    resources = payload.get("resources")
    if not isinstance(resources, list):
        resources = []
    return {
        "project_root": str(root),
        "default_resource_alias": payload.get("default_resource_alias"),
        "resource_count": len(resources),
        "resources": resources,
    }


def project_bindings_resource_text(project_root: str | Path | None = None) -> str:
    """Return the bindings payload as pretty-printed JSON text."""
    return json.dumps(
        build_list_bindings_payload(project_root),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# High-level convenience functions (resolve client internally when needed)
# ---------------------------------------------------------------------------


def _resolve_client(
    client: NotionClient | None,
    project_root: str | Path | None,
) -> tuple[NotionClient, str]:
    """Return a ``(client, project_root_str)`` pair.

    When *client* is ``None`` a new one is created from the project's stored
    credentials.  When *client* is provided, *project_root* is required.
    """
    if client is None:
        from .auth_flow import notion_client_for_project

        client, context = notion_client_for_project(project_root)
        return client, str(context["project_root"])
    if project_root is None:
        raise LabbookError("project_root is required when client is provided.")
    return client, str(project_root)


def search_resources(
    *,
    client: NotionClient | None = None,
    project_root: str | Path | None = None,
    query: str | None = None,
    page_size: int | str | None = None,
) -> dict[str, Any]:
    """Search the workspace, resolving a client if not provided."""
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_search_resources_payload(
        resolved_client,
        project_root=root_str,
        query=query,
        page_size=page_size,
    )


def discover_children(
    *,
    client: NotionClient | None = None,
    project_root: str | Path | None = None,
    resource_id_or_url: str,
    resource_type: str | None = None,
    limit: int | str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Discover children, resolving a client if not provided."""
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_discover_children_payload(
        resolved_client,
        project_root=root_str,
        resource_id_or_url=resource_id_or_url,
        resource_type=resource_type,
        limit=limit,
        mode=mode,
    )


def bind_resource_urls(
    *,
    client: NotionClient | None = None,
    project_root: str | Path | None = None,
    resource_urls: list[str],
    selection_scope: str | None = "subtree",
    default_alias: str | None = None,
) -> dict[str, Any]:
    """Bind resources by URL, resolving a client if not provided."""
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_bind_resource_urls_payload(
        resolved_client,
        project_root=root_str,
        resource_urls=resource_urls,
        selection_scope=selection_scope,
        default_alias=default_alias,
    )


def bind_resources(
    *,
    client: NotionClient | None = None,
    project_root: str | Path | None = None,
    resource_refs: list[dict[str, Any]],
    default_alias: str | None = None,
) -> dict[str, Any]:
    """Bind resources by ref, resolving a client if not provided."""
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_bind_resources_payload(
        resolved_client,
        project_root=root_str,
        resource_refs=resource_refs,
        default_alias=default_alias,
    )


def list_bindings(project_root: str | Path | None = None) -> dict[str, Any]:
    """List current bindings (no Notion API call)."""
    return build_list_bindings_payload(project_root)
