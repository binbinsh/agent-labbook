from __future__ import annotations

from pathlib import Path
from typing import Any

from .binding_discovery import (
    _normalize_notion_resource,
    _resource_type,
    _resource_title,
    discover_children_for_resource,
)
from .notion_api import NOTION_API_BASE, NotionClient
from .state import (
    LabbookError,
    load_project_bindings,
    normalize_notion_id,
    resolve_project_root,
    save_project_bindings,
)


DEFAULT_SEARCH_PAGE_SIZE = 25
MIN_SEARCH_PAGE_SIZE = 1
MAX_SEARCH_PAGE_SIZE = 100
DEFAULT_DISCOVERY_LIMIT = 50
MIN_DISCOVERY_LIMIT = 1
MAX_DISCOVERY_LIMIT = 200


def normalize_search_page_size(page_size: int | str | None = None) -> int:
    if page_size in (None, ""):
        return DEFAULT_SEARCH_PAGE_SIZE
    try:
        limit = int(page_size)
    except (TypeError, ValueError) as exc:
        raise LabbookError("page_size must be an integer number of results.") from exc
    return min(max(limit, MIN_SEARCH_PAGE_SIZE), MAX_SEARCH_PAGE_SIZE)


def normalize_discovery_limit(limit: int | str | None = None) -> int:
    if limit in (None, ""):
        return DEFAULT_DISCOVERY_LIMIT
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise LabbookError("limit must be an integer number of results.") from exc
    return min(max(parsed_limit, MIN_DISCOVERY_LIMIT), MAX_DISCOVERY_LIMIT)


def _endpoint_for_resource(resource_type: str | None, resource_id: str) -> str | None:
    clean_id = normalize_notion_id(resource_id)
    if resource_type == "page":
        return f"{NOTION_API_BASE}/pages/{clean_id}"
    if resource_type == "data_source":
        return f"{NOTION_API_BASE}/data_sources/{clean_id}"
    return None


def _slugify_alias(text: str, *, fallback: str) -> str:
    import re

    candidate = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return candidate or fallback


def _normalize_selection_scope(value: Any) -> str:
    clean_value = str(value or "resource").strip().lower()
    if clean_value not in {"resource", "subtree"}:
        raise LabbookError("selection_scope must be 'resource' or 'subtree'.")
    return clean_value


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    if len(resources) != 1:
        return None
    return str(resources[0].get("alias") or "").strip() or None


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
    from datetime import datetime, timezone

    clean_id = normalize_notion_id(resource_id)
    clean_type = str(resource_type or "").strip().lower()
    if clean_type == "database":
        clean_type = "data_source"
    if clean_type not in {"page", "data_source"}:
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


def _existing_bindings(project_root: Path) -> list[dict[str, Any]]:
    bindings_payload = load_project_bindings(project_root) or {}
    resources = bindings_payload.get("resources")
    if not isinstance(resources, list):
        return []
    return [item for item in resources if isinstance(item, dict)]


def _alias_for_resource(
    *,
    resource_id: str,
    proposed_alias: str,
    used_aliases: set[str],
    alias_owner: dict[str, str],
) -> str:
    if (
        proposed_alias not in used_aliases
        or alias_owner.get(proposed_alias) == resource_id
    ):
        used_aliases.add(proposed_alias)
        alias_owner[proposed_alias] = resource_id
        return proposed_alias

    suffix = 2
    while True:
        candidate = f"{proposed_alias}-{suffix}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            alias_owner[candidate] = resource_id
            return candidate
        suffix += 1


def _sorted_bindings(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    return {
        "project_root": str(project_root),
        "default_resource_alias": _default_resource_alias(resources),
        "resources": _sorted_bindings(resources),
    }


def _normalize_resource_input(item: dict[str, Any]) -> dict[str, str | None]:
    resource_ref = str(
        item.get("resource_id_or_url")
        or item.get("resource_id")
        or item.get("resource_url")
        or ""
    ).strip()
    if not resource_ref:
        raise LabbookError(
            "Each resource_refs item must include resource_id_or_url, resource_id, or resource_url."
        )
    resource_id = normalize_notion_id(resource_ref)
    resource_type = str(item.get("resource_type") or "").strip().lower() or None
    if resource_type == "database":
        resource_type = "data_source"
    if resource_type not in {None, "page", "data_source"}:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    alias = str(item.get("alias") or "").strip() or None
    title = str(item.get("title") or "").strip() or None
    selection_scope = _normalize_selection_scope(item.get("selection_scope"))
    return {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resource_url": str(item.get("resource_url") or "").strip() or None,
        "alias": alias,
        "title": title,
        "selection_scope": selection_scope,
    }


def build_search_resources_payload(
    client: NotionClient,
    *,
    project_root: str,
    query: str | None = None,
    page_size: int | str | None = None,
) -> dict[str, Any]:
    page_limit = normalize_search_page_size(page_size)
    payload = client.search(query=query, page_size=page_limit)
    results: list[dict[str, Any]] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        normalized = _normalize_notion_resource(item)
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


def _rank_search_results(
    results: list[dict[str, Any]], *, query: str | None = None
) -> list[dict[str, Any]]:
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


def build_discover_children_payload(
    client: NotionClient,
    *,
    project_root: str,
    resource_id_or_url: str,
    resource_type: str | None = None,
    limit: int | str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
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
    if not resource_refs:
        raise LabbookError("resource_refs must contain at least one resource.")

    root = resolve_project_root(project_root)
    existing_resources = _existing_bindings(root)
    by_resource_id = {
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
        normalized_resource = _normalize_notion_resource(resource)
        if normalized_resource is None:
            raise LabbookError(
                f"Resource {normalized_input['resource_id']} is not a page or data source."
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
                "resource_id_or_url": resource_url,
                "selection_scope": _normalize_selection_scope(selection_scope),
            }
            for resource_url in normalized_urls
        ],
        default_alias=default_alias,
    )


def build_list_bindings_payload(project_root: str | Path | None = None) -> dict[str, Any]:
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
    import json

    return json.dumps(
        build_list_bindings_payload(project_root),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def search_resources(
    *,
    client: NotionClient | None = None,
    project_root: str | Path | None = None,
    query: str | None = None,
    page_size: int | str | None = None,
) -> dict[str, Any]:
    if client is None:
        from .auth_flow import _notion_client

        client, context = _notion_client(project_root)
        project_root = str(context["project_root"])
    elif project_root is None:
        raise LabbookError("project_root is required when client is provided.")
    return build_search_resources_payload(
        client,
        project_root=str(project_root),
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
    if client is None:
        from .auth_flow import _notion_client

        client, context = _notion_client(project_root)
        project_root = str(context["project_root"])
    elif project_root is None:
        raise LabbookError("project_root is required when client is provided.")
    return build_discover_children_payload(
        client,
        project_root=str(project_root),
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
    if client is None:
        from .auth_flow import _notion_client

        client, context = _notion_client(project_root)
        project_root = str(context["project_root"])
    elif project_root is None:
        raise LabbookError("project_root is required when client is provided.")
    return build_bind_resource_urls_payload(
        client,
        project_root=str(project_root),
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
    if client is None:
        from .auth_flow import _notion_client

        client, context = _notion_client(project_root)
        project_root = str(context["project_root"])
    elif project_root is None:
        raise LabbookError("project_root is required when client is provided.")
    return build_bind_resources_payload(
        client,
        project_root=str(project_root),
        resource_refs=resource_refs,
        default_alias=default_alias,
    )


def list_bindings(project_root: str | Path | None = None) -> dict[str, Any]:
    return build_list_bindings_payload(project_root)
