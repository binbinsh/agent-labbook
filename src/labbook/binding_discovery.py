from __future__ import annotations

import logging
from collections import deque
from typing import Any

from .notion_api import NotionClient
from .state import LabbookError, normalize_notion_id

logger = logging.getLogger("labbook.binding_discovery")


MAX_DISCOVERY_BLOCK_SCAN_LIMIT = 20_000
_MAX_BLOCK_CHILDREN_PER_CONTAINER = 50_000


def _rich_text_to_plain_text(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    text = "".join(
        str(item.get("plain_text") or "") for item in items if isinstance(item, dict)
    ).strip()
    return text or None


def resource_title(resource: dict[str, Any]) -> str | None:
    object_type = str(resource.get("object") or "").strip().lower()
    properties = resource.get("properties")
    if object_type == "page":
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
        if isinstance(properties, dict):
            for value in properties.values():
                if isinstance(value, dict) and value.get("type") == "title":
                    title = _rich_text_to_plain_text(value.get("title"))
                    if title:
                        return title
    if object_type in {"data_source", "database"}:
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
    title = str(resource.get("name") or "").strip()
    return title or None


def resource_type_of(resource: dict[str, Any]) -> str:
    """Return the canonical resource type for a Notion object.

    Notion's API transitioned from ``"database"`` to ``"data_source"`` as the
    primary queryable entity (Notion-Version 2026-03-11).  The older search and
    block-children endpoints still emit ``"object": "database"`` and
    ``"child_database"`` block types, while the newer data-source endpoints use
    ``"object": "data_source"``.

    This function normalises both terms to ``"data_source"`` so the rest of the
    codebase only needs to handle two canonical types: ``"page"`` and
    ``"data_source"``.  Note that the *container* concept (a Notion database
    that holds one or more data-sources) is intentionally preserved via the
    separate ``parent_database_id`` metadata field.
    """
    raw = (
        str(resource.get("object") or resource.get("resource_type") or "")
        .strip()
        .lower()
    )
    if raw == "database":
        return "data_source"
    if raw in {"page", "data_source"}:
        return raw
    return "unknown"


def normalize_notion_resource(resource: dict[str, Any]) -> dict[str, Any] | None:
    resource_type = resource_type_of(resource)
    if resource_type not in {"page", "data_source"}:
        return None
    resource_id = normalize_notion_id(
        str(resource.get("id") or resource.get("resource_id") or "")
    )
    title = resource_title(resource) or resource_id
    resource_url = (
        str(resource.get("url") or resource.get("resource_url") or "").strip() or None
    )
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resource_url": resource_url,
        "title": title,
        "last_edited_time": resource.get("last_edited_time"),
        "parent": resource.get("parent"),
    }


def _safe_notion_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return normalize_notion_id(raw)
    except LabbookError:
        return None


def _normalize_parent_metadata(parent: Any) -> dict[str, Any]:
    if not isinstance(parent, dict):
        return {
            "parent_type": None,
            "parent_id": None,
            "parent_database_id": None,
        }

    parent_type = str(parent.get("type") or "").strip() or None
    if not parent_type or parent_type == "workspace":
        return {
            "parent_type": parent_type,
            "parent_id": None,
            "parent_database_id": None,
        }

    if parent_type == "page_id":
        return {
            "parent_type": parent_type,
            "parent_id": _safe_notion_id(parent.get("page_id")),
            "parent_database_id": None,
        }

    if parent_type == "data_source_id":
        return {
            "parent_type": parent_type,
            "parent_id": _safe_notion_id(parent.get("data_source_id")),
            "parent_database_id": _safe_notion_id(parent.get("database_id")),
        }

    if parent_type == "database_id":
        normalized_id = _safe_notion_id(parent.get("database_id"))
        return {
            "parent_type": parent_type,
            "parent_id": normalized_id,
            "parent_database_id": normalized_id,
        }

    if parent_type == "block_id":
        return {
            "parent_type": parent_type,
            "parent_id": _safe_notion_id(parent.get("block_id")),
            "parent_database_id": None,
        }

    return {
        "parent_type": parent_type,
        "parent_id": None,
        "parent_database_id": None,
    }


def _normalize_discovery_resource(
    resource: dict[str, Any],
    *,
    resource_id: str | None = None,
    resource_type: str | None = None,
    resource_url: str | None = None,
    title: str | None = None,
    parent: dict[str, Any] | None = None,
    parent_type: str | None = None,
    parent_id: str | None = None,
    parent_database_id: str | None = None,
    discovered_parent_id: str | None = None,
    discovered_root_id: str | None = None,
    discovered_depth: int | None = None,
) -> dict[str, Any] | None:
    normalized = normalize_notion_resource(
        {
            **resource,
            "id": resource_id or resource.get("id"),
            "object": resource_type or resource.get("object"),
            "resource_type": resource_type or resource.get("resource_type"),
            "url": resource_url or resource.get("url") or resource.get("resource_url"),
            "resource_url": resource_url or resource.get("resource_url"),
            "parent": parent if parent is not None else resource.get("parent"),
            "title": resource.get("title"),
        }
    )
    if normalized is None:
        return None

    base_parent = _normalize_parent_metadata(
        parent if parent is not None else resource.get("parent")
    )
    if parent_type is not None:
        base_parent["parent_type"] = parent_type
    if parent_id is not None:
        base_parent["parent_id"] = _safe_notion_id(parent_id)
    if parent_database_id is not None:
        base_parent["parent_database_id"] = _safe_notion_id(parent_database_id)

    normalized["title"] = (
        str(title or normalized["title"]).strip() or normalized["resource_id"]
    )
    normalized["parent_type"] = base_parent["parent_type"]
    normalized["parent_id"] = base_parent["parent_id"]
    normalized["parent_database_id"] = base_parent["parent_database_id"]
    normalized["discovered_parent_id"] = _safe_notion_id(discovered_parent_id)
    normalized["discovered_root_id"] = _safe_notion_id(discovered_root_id)
    normalized["discovered_depth"] = (
        int(discovered_depth)
        if isinstance(discovered_depth, int) and discovered_depth >= 0
        else None
    )
    return normalized


def merge_discovery_resources(
    *resource_lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for resources in resource_lists:
        for item in resources:
            if not isinstance(item, dict):
                continue
            resource_id = str(item.get("resource_id") or "").strip()
            if not resource_id:
                continue
            by_id[resource_id] = {
                **by_id.get(resource_id, {}),
                **item,
            }

    return sorted(
        by_id.values(),
        key=lambda item: (
            {"page": 0, "data_source": 1}.get(str(item.get("resource_type")), 2),
            str(item.get("title") or "").lower(),
            str(item.get("resource_id") or "").lower(),
        ),
    )


def _list_all_block_children(
    client: NotionClient,
    block_id: str,
    *,
    max_items: int = _MAX_BLOCK_CHILDREN_PER_CONTAINER,
) -> list[dict[str, Any]]:
    """Paginate through all direct children of *block_id*.

    An optional *max_items* cap prevents runaway memory usage for
    programmatically-generated pages with an extreme number of blocks.
    """
    results: list[dict[str, Any]] = []
    next_cursor: str | None = None

    while True:
        payload = client.list_block_children(
            block_id,
            page_size=100,
            start_cursor=next_cursor,
        )
        children = payload.get("results")
        if isinstance(children, list):
            results.extend(item for item in children if isinstance(item, dict))

        if len(results) >= max_items:
            logger.warning(
                "Reached per-container child limit (%d) for block %s; "
                "truncating results",
                max_items,
                block_id[:8],
            )
            break

        if not payload.get("has_more") or not payload.get("next_cursor"):
            break
        next_cursor = str(payload.get("next_cursor") or "").strip() or None
        if not next_cursor:
            break

    return results


def _list_initial_block_children(
    client: NotionClient,
    block_id: str,
    *,
    page_size: int = 100,
) -> tuple[list[dict[str, Any]], bool]:
    payload = client.list_block_children(
        block_id, page_size=page_size, start_cursor=None
    )
    children = payload.get("results")
    if not isinstance(children, list):
        return [], False
    return [item for item in children if isinstance(item, dict)], bool(
        payload.get("has_more")
    )


def _query_data_source_entries(
    client: NotionClient,
    data_source_id: str,
    *,
    remaining_limit: int,
    discovery_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    next_cursor: str | None = None
    meta = discovery_meta or {}

    while len(resources) < remaining_limit:
        payload = client.query_data_source(
            data_source_id,
            page_size=min(100, max(1, remaining_limit - len(resources))),
            start_cursor=next_cursor,
        )
        results = payload.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_discovery_resource(
                    item,
                    discovered_parent_id=meta.get("discovered_parent_id")
                    or data_source_id,
                    discovered_root_id=meta.get("discovered_root_id") or data_source_id,
                    discovered_depth=meta.get("discovered_depth", 1),
                    parent_type=meta.get("parent_type"),
                    parent_id=meta.get("parent_id"),
                    parent_database_id=meta.get("parent_database_id"),
                )
                if not normalized:
                    continue
                resource_id = str(normalized.get("resource_id") or "")
                if resource_id in seen_ids:
                    continue
                seen_ids.add(resource_id)
                resources.append(normalized)
                if len(resources) >= remaining_limit:
                    break

        if not payload.get("has_more") or not payload.get("next_cursor"):
            break
        next_cursor = str(payload.get("next_cursor") or "").strip() or None
        if not next_cursor:
            break

    return resources


def _retrieve_page_resource(
    client: NotionClient,
    page_id: str,
    *,
    fallback_title: str | None = None,
    discovery_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = discovery_meta or {}
    try:
        payload = client.retrieve_page(page_id)
        normalized = _normalize_discovery_resource(
            payload,
            resource_type="page",
            title=resource_title(payload) or fallback_title,
            discovered_parent_id=meta.get("discovered_parent_id"),
            discovered_root_id=meta.get("discovered_root_id"),
            discovered_depth=meta.get("discovered_depth"),
            parent_type=meta.get("parent_type"),
            parent_id=meta.get("parent_id"),
            parent_database_id=meta.get("parent_database_id"),
        )
        if normalized is not None:
            return normalized
    except LabbookError:
        pass

    fallback_payload = {
        "id": page_id,
        "object": "page",
        "url": None,
        "parent": {
            "type": "page_id",
            "page_id": meta.get("discovered_parent_id"),
        },
    }
    normalized = _normalize_discovery_resource(
        fallback_payload,
        resource_type="page",
        title=fallback_title or f"Child page {page_id[:8]}",
        discovered_parent_id=meta.get("discovered_parent_id"),
        discovered_root_id=meta.get("discovered_root_id"),
        discovered_depth=meta.get("discovered_depth"),
        parent_type=meta.get("parent_type"),
        parent_id=meta.get("parent_id"),
        parent_database_id=meta.get("parent_database_id"),
    )
    if normalized is None:
        raise LabbookError(f"Could not normalize page resource {page_id}.")
    return normalized


def _fast_child_page_resource(
    block: dict[str, Any],
    *,
    page_id: str,
    parent_page_id: str,
    root_id: str,
    depth: int,
) -> dict[str, Any]:
    normalized = _normalize_discovery_resource(
        {
            "id": page_id,
            "object": "page",
            "url": None,
            "parent": {
                "type": "page_id",
                "page_id": parent_page_id,
            },
        },
        resource_type="page",
        title=str(((block.get("child_page") or {}).get("title") or "")).strip()
        or f"Child page {page_id[:8]}",
        discovered_parent_id=parent_page_id,
        discovered_root_id=root_id,
        discovered_depth=depth + 1,
        parent_type="page_id",
        parent_id=parent_page_id,
    )
    if normalized is None:
        raise LabbookError(f"Could not normalize shallow child page {page_id}.")
    return normalized


def _retrieve_data_source_resources_for_database(
    client: NotionClient,
    database_id: str,
    *,
    discovery_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    meta = discovery_meta or {}
    try:
        database_payload = client.retrieve_database(database_id)
    except LabbookError:
        return []

    data_sources = (
        database_payload.get("data_sources")
        if isinstance(database_payload.get("data_sources"), list)
        else []
    )
    resources: list[dict[str, Any]] = []

    for item in data_sources:
        if not isinstance(item, dict):
            continue
        data_source_id = normalize_notion_id(str(item.get("id") or ""))
        if not data_source_id:
            continue
        try:
            payload = client.retrieve_data_source(data_source_id)
            normalized = _normalize_discovery_resource(
                payload,
                resource_type="data_source",
                title=resource_title(payload) or str(item.get("name") or "").strip(),
                discovered_parent_id=meta.get("discovered_parent_id"),
                discovered_root_id=meta.get("discovered_root_id"),
                discovered_depth=meta.get("discovered_depth"),
                parent_type=meta.get("parent_type"),
                parent_id=meta.get("parent_id"),
                parent_database_id=meta.get("parent_database_id"),
            )
        except LabbookError:
            fallback_payload = {
                "id": data_source_id,
                "object": "data_source",
                "url": None,
                "parent": {
                    "type": "page_id",
                    "page_id": meta.get("discovered_parent_id")
                    or meta.get("parent_id"),
                },
            }
            normalized = _normalize_discovery_resource(
                fallback_payload,
                resource_type="data_source",
                title=str(item.get("name") or "").strip()
                or f"Data source {data_source_id[:8]}",
                discovered_parent_id=meta.get("discovered_parent_id"),
                discovered_root_id=meta.get("discovered_root_id"),
                discovered_depth=meta.get("discovered_depth"),
                parent_type=meta.get("parent_type"),
                parent_id=meta.get("parent_id"),
                parent_database_id=meta.get("parent_database_id"),
            )
        if normalized is not None:
            resources.append(normalized)

    return resources


def _discover_page_immediate_children(
    client: NotionClient,
    page_id: str,
    *,
    root_id: str,
    depth: int,
    remaining_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    resources: list[dict[str, Any]] = []
    container_queue: deque[str] = deque([page_id])
    scanned_containers: set[str] = set()
    scanned_block_count = 0
    partial = False

    while container_queue and len(resources) < remaining_limit:
        container_id = str(container_queue.popleft() or "").strip()
        if not container_id or container_id in scanned_containers:
            continue
        scanned_containers.add(container_id)

        blocks = _list_all_block_children(client, container_id)
        scanned_block_count += len(blocks)
        if scanned_block_count > MAX_DISCOVERY_BLOCK_SCAN_LIMIT:
            partial = True
            break

        for block in blocks:
            block_id = normalize_notion_id(str(block.get("id") or ""))
            block_type = str(block.get("type") or "").strip()

            if block_type == "child_page" and block_id:
                resources.append(
                    _retrieve_page_resource(
                        client,
                        block_id,
                        fallback_title=str(
                            (block.get("child_page") or {}).get("title") or ""
                        ).strip()
                        or None,
                        discovery_meta={
                            "discovered_parent_id": page_id,
                            "discovered_root_id": root_id,
                            "discovered_depth": depth + 1,
                            "parent_type": "page_id",
                            "parent_id": page_id,
                        },
                    )
                )
            elif block_type == "child_database" and block_id:
                resources.extend(
                    _retrieve_data_source_resources_for_database(
                        client,
                        block_id,
                        discovery_meta={
                            "discovered_parent_id": page_id,
                            "discovered_root_id": root_id,
                            "discovered_depth": depth + 1,
                            "parent_type": "page_id",
                            "parent_id": page_id,
                        },
                    )
                )
            elif bool(block.get("has_children")) and block_id:
                container_queue.append(block_id)

            if len(resources) >= remaining_limit:
                partial = bool(container_queue)
                break

    return resources[:remaining_limit], partial


def _discover_page_shallow_children(
    client: NotionClient,
    page_id: str,
    *,
    root_id: str,
    depth: int,
    remaining_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    resources: list[dict[str, Any]] = []
    blocks, has_more = _list_initial_block_children(client, page_id, page_size=100)
    deeper_candidates = has_more

    for block in blocks:
        block_id = normalize_notion_id(str(block.get("id") or ""))
        block_type = str(block.get("type") or "").strip()

        if block_type == "child_page" and block_id:
            resources.append(
                _fast_child_page_resource(
                    block,
                    page_id=block_id,
                    parent_page_id=page_id,
                    root_id=root_id,
                    depth=depth,
                )
            )
        elif block_type == "child_database" and block_id:
            deeper_candidates = True
        elif bool(block.get("has_children")) and block_id:
            deeper_candidates = True

        if len(resources) >= remaining_limit:
            break

    return resources[:remaining_limit], deeper_candidates


def _discover_data_source_immediate_children(
    client: NotionClient,
    data_source_id: str,
    *,
    root_id: str,
    depth: int,
    remaining_limit: int,
) -> list[dict[str, Any]]:
    return _query_data_source_entries(
        client,
        data_source_id,
        remaining_limit=remaining_limit,
        discovery_meta={
            "discovered_parent_id": data_source_id,
            "discovered_root_id": root_id,
            "discovered_depth": depth + 1,
            "parent_type": "data_source_id",
            "parent_id": data_source_id,
        },
    )


def discover_children_for_resource(
    client: NotionClient,
    root_resource: dict[str, Any],
    *,
    page_limit: int,
    mode: str = "shallow",
) -> dict[str, Any]:
    clean_mode = str(mode or "shallow").strip().lower()
    if clean_mode not in {"shallow", "deep"}:
        raise LabbookError("mode must be 'shallow' or 'deep'.")

    normalized_root = _normalize_discovery_resource(
        root_resource,
        discovered_root_id=normalize_notion_id(str(root_resource.get("id") or "")),
        discovered_depth=0,
    )
    if normalized_root is None:
        raise LabbookError("The selected resource is not a page or data source.")

    if normalized_root["resource_type"] == "page":
        if clean_mode == "deep":
            discovered, partial = _discover_page_immediate_children(
                client,
                normalized_root["resource_id"],
                root_id=normalized_root["resource_id"],
                depth=0,
                remaining_limit=page_limit,
            )
        else:
            discovered, partial = _discover_page_shallow_children(
                client,
                normalized_root["resource_id"],
                root_id=normalized_root["resource_id"],
                depth=0,
                remaining_limit=page_limit,
            )
    else:
        discovered = _discover_data_source_immediate_children(
            client,
            normalized_root["resource_id"],
            root_id=normalized_root["resource_id"],
            depth=0,
            remaining_limit=page_limit,
        )
        partial = False

    return {
        "root_resource": normalized_root,
        "page_size": page_limit,
        "mode": clean_mode,
        "partial": partial,
        "result_count": len(discovered),
        "results": merge_discovery_resources(discovered),
    }
