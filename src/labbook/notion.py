"""Notion API client, resource discovery, and binding operations."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .state import (
    DEFAULT_NOTION_VERSION,
    LabbookError,
    bindings_path,
    load_project_bindings,
    normalize_notion_id,
    resolve_project_root,
    save_project_bindings,
)

logger = logging.getLogger("labbook.notion")

NOTION_API_BASE = "https://api.notion.com/v1"
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 3
_INITIAL_BACKOFF_SECONDS = 1.0
_BACKOFF_MULTIPLIER = 2.0
DEFAULT_SEARCH_PAGE_SIZE = 25
MAX_DISCOVERY_BLOCK_SCAN_LIMIT = 20_000
_MAX_BLOCK_CHILDREN_PER_CONTAINER = 50_000


class NotionApiError(LabbookError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


class NotionClient:
    def __init__(
        self,
        *,
        token: str,
        notion_version: str = DEFAULT_NOTION_VERSION,
        timeout: float = 30.0,
    ) -> None:
        self.token = token.strip()
        self.notion_version = notion_version
        self.timeout = timeout
        if not self.token:
            raise NotionApiError("Missing Notion integration secret.")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{NOTION_API_BASE}{path}"
        data = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        last_exc: Exception | None = None
        backoff = _INITIAL_BACKOFF_SECONDS
        for attempt in range(_MAX_RETRIES + 1):
            req = request.Request(
                url, data=data, headers=self._headers(), method=method.upper()
            )
            try:
                with request.urlopen(req, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                if not raw.strip():
                    return {}
                try:
                    decoded = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise NotionApiError("Notion API returned invalid JSON.") from exc
                if not isinstance(decoded, dict):
                    raise NotionApiError("Notion API returned an unexpected payload.")
                return decoded
            except error.HTTPError as exc:
                raw_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        wait = max(0.0, float(retry_after)) if retry_after else backoff
                    except (TypeError, ValueError):
                        wait = backoff
                    time.sleep(wait)
                    backoff *= _BACKOFF_MULTIPLIER
                    last_exc = exc
                    continue
                try:
                    parsed_error = json.loads(raw_body)
                except json.JSONDecodeError:
                    parsed_error = {"message": raw_body}
                message = parsed_error.get("message") or str(exc)
                raise NotionApiError(
                    f"Notion API {exc.code}: {message}", status_code=exc.code
                ) from exc
            except error.URLError as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= _BACKOFF_MULTIPLIER
                    last_exc = exc
                    continue
                raise NotionApiError(
                    f"Could not reach Notion API: {exc.reason}"
                ) from exc
        raise NotionApiError(
            f"Notion API request failed after {_MAX_RETRIES + 1} attempts."
        ) from last_exc

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/users/me")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{normalize_notion_id(page_id)}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/data_sources/{normalize_notion_id(data_source_id)}"
        )

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{normalize_notion_id(database_id)}")

    def search(
        self,
        *,
        query: str | None = None,
        page_size: int = 25,
        start_cursor: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "page_size": page_size,
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        clean_query = str(query or "").strip()
        if clean_query:
            body["query"] = clean_query
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._request("POST", "/search", body=body)

    def search_all(
        self, *, query: str | None = None, max_results: int = 10_000
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(results) < max_results:
            payload = self.search(query=query, page_size=100, start_cursor=cursor)
            for item in payload.get("results", []):
                results.append(item)
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        return results[:max_results]

    def list_block_children(
        self, block_id: str, *, page_size: int = 100, start_cursor: str | None = None
    ) -> dict[str, Any]:
        query = {"page_size": page_size}
        if start_cursor:
            query["start_cursor"] = start_cursor
        return self._request(
            "GET",
            f"/blocks/{normalize_notion_id(block_id)}/children?{parse.urlencode(query)}",
        )

    def query_data_source(
        self,
        data_source_id: str,
        *,
        page_size: int = 100,
        start_cursor: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"page_size": page_size}
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._request(
            "POST",
            f"/data_sources/{normalize_notion_id(data_source_id)}/query",
            body=body,
        )

    def retrieve_resource(
        self, resource_id: str, resource_type: str | None = None
    ) -> dict[str, Any]:
        t = str(resource_type or "").strip().lower()
        nid = normalize_notion_id(resource_id)
        if t == "page":
            return self.retrieve_page(nid)
        if t in {"data_source", "database"}:
            return self.retrieve_data_source(nid)
        try:
            return self.retrieve_page(nid)
        except NotionApiError as exc:
            if exc.status_code not in {400, 404}:
                raise
        return self.retrieve_data_source(nid)

    def resolve_bindable_data_source(self, resource_id: str) -> dict[str, Any]:
        nid = normalize_notion_id(resource_id)
        try:
            return self.retrieve_data_source(nid)
        except NotionApiError as exc:
            if exc.status_code not in {400, 404}:
                raise
            database_payload = self.retrieve_database(nid)
        data_sources = (
            database_payload.get("data_sources")
            if isinstance(database_payload.get("data_sources"), list)
            else []
        )
        resolved: list[tuple[str, str]] = []
        for item in data_sources:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or "").strip()
            if not raw_id:
                continue
            try:
                ds_id = normalize_notion_id(raw_id)
            except LabbookError:
                continue
            resolved.append(
                (
                    ds_id,
                    str(item.get("name") or "").strip() or f"Data source {ds_id[:8]}",
                )
            )
        if not resolved:
            raise LabbookError(
                f"Resource {nid} is a database container with no bindable data sources. Expand it in the chooser and select a concrete page or data source."
            )
        if len(resolved) > 1:
            raise LabbookError(
                f"Resource {nid} is a database container with multiple data sources. Expand it in the chooser and select the specific data source you want to bind."
            )
        ds_id, ds_title = resolved[0]
        try:
            return self.retrieve_data_source(ds_id)
        except NotionApiError as exc:
            if exc.status_code not in {400, 404}:
                raise
            return {"object": "data_source", "id": ds_id, "name": ds_title, "url": None}

    def resolve_bindable_resource(
        self, resource_id: str, resource_type: str | None = None
    ) -> tuple[dict[str, Any], str]:
        t = str(resource_type or "").strip().lower()
        if t == "database":
            t = "data_source"
        if t == "page":
            return self.retrieve_page(resource_id), "page"
        if t == "data_source":
            return self.resolve_bindable_data_source(resource_id), "data_source"
        resource = self.retrieve_resource(resource_id, resource_type)
        nr = normalize_notion_resource(resource)
        if nr is None:
            raise LabbookError(
                f"Resource {normalize_notion_id(resource_id)} is not a page or data source."
            )
        return resource, str(nr["resource_type"])


# ---------------------------------------------------------------------------
# Resource normalization helpers
# ---------------------------------------------------------------------------


def _rich_text_to_plain_text(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    text = "".join(
        str(item.get("plain_text") or "") for item in items if isinstance(item, dict)
    ).strip()
    return text or None


def resource_title(resource: dict[str, Any]) -> str | None:
    obj = str(resource.get("object") or "").strip().lower()
    props = resource.get("properties")
    if obj == "page":
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
        if isinstance(props, dict):
            for v in props.values():
                if isinstance(v, dict) and v.get("type") == "title":
                    title = _rich_text_to_plain_text(v.get("title"))
                    if title:
                        return title
    if obj in {"data_source", "database"}:
        title = _rich_text_to_plain_text(resource.get("title"))
        if title:
            return title
    title = str(resource.get("name") or "").strip()
    return title or None


def resource_type_of(resource: dict[str, Any]) -> str:
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
    rt = resource_type_of(resource)
    if rt not in {"page", "data_source"}:
        return None
    rid = normalize_notion_id(
        str(resource.get("id") or resource.get("resource_id") or "")
    )
    title = resource_title(resource) or rid
    url = str(resource.get("url") or resource.get("resource_url") or "").strip() or None
    return {
        "resource_id": rid,
        "resource_type": rt,
        "resource_url": url,
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


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _normalize_parent_metadata(parent: Any) -> dict[str, Any]:
    if not isinstance(parent, dict):
        return {"parent_type": None, "parent_id": None, "parent_database_id": None}
    pt = str(parent.get("type") or "").strip() or None
    if not pt or pt == "workspace":
        return {"parent_type": pt, "parent_id": None, "parent_database_id": None}
    if pt == "page_id":
        return {
            "parent_type": pt,
            "parent_id": _safe_notion_id(parent.get("page_id")),
            "parent_database_id": None,
        }
    if pt == "data_source_id":
        return {
            "parent_type": pt,
            "parent_id": _safe_notion_id(parent.get("data_source_id")),
            "parent_database_id": _safe_notion_id(parent.get("database_id")),
        }
    if pt == "database_id":
        nid = _safe_notion_id(parent.get("database_id"))
        return {"parent_type": pt, "parent_id": nid, "parent_database_id": nid}
    if pt == "block_id":
        return {
            "parent_type": pt,
            "parent_id": _safe_notion_id(parent.get("block_id")),
            "parent_database_id": None,
        }
    return {"parent_type": pt, "parent_id": None, "parent_database_id": None}


def _normalize_discovery_resource(
    resource: dict[str, Any], **kw: Any
) -> dict[str, Any] | None:
    merged = {**resource}
    if kw.get("resource_id"):
        merged["id"] = kw["resource_id"]
    if kw.get("resource_type"):
        merged["object"] = kw["resource_type"]
        merged["resource_type"] = kw["resource_type"]
    if kw.get("resource_url"):
        merged["url"] = kw["resource_url"]
        merged["resource_url"] = kw["resource_url"]
    if "parent" in kw and kw["parent"] is not None:
        merged["parent"] = kw["parent"]
    normalized = normalize_notion_resource(merged)
    if normalized is None:
        return None
    base_parent = _normalize_parent_metadata(
        kw.get("parent") if "parent" in kw else resource.get("parent")
    )
    for key in ("parent_type", "parent_id", "parent_database_id"):
        if kw.get(key) is not None:
            base_parent[key] = (
                _safe_notion_id(kw[key]) if key != "parent_type" else kw[key]
            )
    if kw.get("title"):
        normalized["title"] = str(kw["title"]).strip() or normalized["resource_id"]
    normalized.update(base_parent)
    normalized["discovered_parent_id"] = _safe_notion_id(kw.get("discovered_parent_id"))
    normalized["discovered_root_id"] = _safe_notion_id(kw.get("discovered_root_id"))
    dd = kw.get("discovered_depth")
    normalized["discovered_depth"] = (
        int(dd) if isinstance(dd, int) and dd >= 0 else None
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
            rid = str(item.get("resource_id") or "").strip()
            if rid:
                by_id[rid] = {**by_id.get(rid, {}), **item}
    return sorted(
        by_id.values(),
        key=lambda i: (
            {"page": 0, "data_source": 1}.get(str(i.get("resource_type")), 2),
            str(i.get("title") or "").lower(),
            str(i.get("resource_id") or "").lower(),
        ),
    )


def _list_all_block_children(
    client: NotionClient,
    block_id: str,
    *,
    max_items: int = _MAX_BLOCK_CHILDREN_PER_CONTAINER,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_cursor: str | None = None
    while True:
        payload = client.list_block_children(
            block_id, page_size=100, start_cursor=next_cursor
        )
        children = payload.get("results")
        if isinstance(children, list):
            results.extend(item for item in children if isinstance(item, dict))
        if len(results) >= max_items:
            break
        if not payload.get("has_more") or not payload.get("next_cursor"):
            break
        next_cursor = str(payload.get("next_cursor") or "").strip() or None
        if not next_cursor:
            break
    return results


def _list_initial_block_children(
    client: NotionClient, block_id: str, *, page_size: int = 100
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
                n = _normalize_discovery_resource(
                    item,
                    discovered_parent_id=meta.get("discovered_parent_id")
                    or data_source_id,
                    discovered_root_id=meta.get("discovered_root_id") or data_source_id,
                    discovered_depth=meta.get("discovered_depth", 1),
                    parent_type=meta.get("parent_type"),
                    parent_id=meta.get("parent_id"),
                    parent_database_id=meta.get("parent_database_id"),
                )
                if not n:
                    continue
                rid = str(n.get("resource_id") or "")
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                resources.append(n)
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
        n = _normalize_discovery_resource(
            payload,
            resource_type="page",
            title=resource_title(payload) or fallback_title,
            **{
                k: meta.get(k)
                for k in (
                    "discovered_parent_id",
                    "discovered_root_id",
                    "discovered_depth",
                    "parent_type",
                    "parent_id",
                    "parent_database_id",
                )
            },
        )
        if n is not None:
            return n
    except LabbookError:
        pass
    fallback = {
        "id": page_id,
        "object": "page",
        "url": None,
        "parent": {"type": "page_id", "page_id": meta.get("discovered_parent_id")},
    }
    n = _normalize_discovery_resource(
        fallback,
        resource_type="page",
        title=fallback_title or f"Child page {page_id[:8]}",
        **{
            k: meta.get(k)
            for k in (
                "discovered_parent_id",
                "discovered_root_id",
                "discovered_depth",
                "parent_type",
                "parent_id",
                "parent_database_id",
            )
        },
    )
    if n is None:
        raise LabbookError(f"Could not normalize page resource {page_id}.")
    return n


def _fast_child_page_resource(
    block: dict[str, Any],
    *,
    page_id: str,
    parent_page_id: str,
    root_id: str,
    depth: int,
) -> dict[str, Any]:
    n = _normalize_discovery_resource(
        {
            "id": page_id,
            "object": "page",
            "url": None,
            "parent": {"type": "page_id", "page_id": parent_page_id},
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
    if n is None:
        raise LabbookError(f"Could not normalize shallow child page {page_id}.")
    return n


def _retrieve_data_source_resources_for_database(
    client: NotionClient,
    database_id: str,
    *,
    discovery_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    meta = discovery_meta or {}
    try:
        db = client.retrieve_database(database_id)
    except LabbookError:
        return []
    data_sources = (
        db.get("data_sources") if isinstance(db.get("data_sources"), list) else []
    )
    resources: list[dict[str, Any]] = []
    for item in data_sources:
        if not isinstance(item, dict):
            continue
        ds_id = normalize_notion_id(str(item.get("id") or ""))
        if not ds_id:
            continue
        try:
            payload = client.retrieve_data_source(ds_id)
            n = _normalize_discovery_resource(
                payload,
                resource_type="data_source",
                title=resource_title(payload) or str(item.get("name") or "").strip(),
                **{
                    k: meta.get(k)
                    for k in (
                        "discovered_parent_id",
                        "discovered_root_id",
                        "discovered_depth",
                        "parent_type",
                        "parent_id",
                        "parent_database_id",
                    )
                },
            )
        except LabbookError:
            fallback = {
                "id": ds_id,
                "object": "data_source",
                "url": None,
                "parent": {
                    "type": "page_id",
                    "page_id": meta.get("discovered_parent_id")
                    or meta.get("parent_id"),
                },
            }
            n = _normalize_discovery_resource(
                fallback,
                resource_type="data_source",
                title=str(item.get("name") or "").strip() or f"Data source {ds_id[:8]}",
                **{
                    k: meta.get(k)
                    for k in (
                        "discovered_parent_id",
                        "discovered_root_id",
                        "discovered_depth",
                        "parent_type",
                        "parent_id",
                        "parent_database_id",
                    )
                },
            )
        if n is not None:
            resources.append(n)
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
        cid = str(container_queue.popleft() or "").strip()
        if not cid or cid in scanned_containers:
            continue
        scanned_containers.add(cid)
        blocks = _list_all_block_children(client, cid)
        scanned_block_count += len(blocks)
        if scanned_block_count > MAX_DISCOVERY_BLOCK_SCAN_LIMIT:
            partial = True
            break
        for block in blocks:
            bid = normalize_notion_id(str(block.get("id") or ""))
            bt = str(block.get("type") or "").strip()
            meta = {
                "discovered_parent_id": page_id,
                "discovered_root_id": root_id,
                "discovered_depth": depth + 1,
                "parent_type": "page_id",
                "parent_id": page_id,
            }
            if bt == "child_page" and bid:
                resources.append(
                    _retrieve_page_resource(
                        client,
                        bid,
                        fallback_title=str(
                            (block.get("child_page") or {}).get("title") or ""
                        ).strip()
                        or None,
                        discovery_meta=meta,
                    )
                )
            elif bt == "child_database" and bid:
                resources.extend(
                    _retrieve_data_source_resources_for_database(
                        client, bid, discovery_meta=meta
                    )
                )
            elif bool(block.get("has_children")) and bid:
                container_queue.append(bid)
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
    deeper = has_more
    for block in blocks:
        bid = normalize_notion_id(str(block.get("id") or ""))
        bt = str(block.get("type") or "").strip()
        if bt == "child_page" and bid:
            resources.append(
                _fast_child_page_resource(
                    block,
                    page_id=bid,
                    parent_page_id=page_id,
                    root_id=root_id,
                    depth=depth,
                )
            )
        elif bt == "child_database" and bid:
            deeper = True
        elif bool(block.get("has_children")) and bid:
            deeper = True
        if len(resources) >= remaining_limit:
            break
    return resources[:remaining_limit], deeper


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
    nr = _normalize_discovery_resource(
        root_resource,
        discovered_root_id=normalize_notion_id(str(root_resource.get("id") or "")),
        discovered_depth=0,
    )
    if nr is None:
        raise LabbookError("The selected resource is not a page or data source.")
    if nr["resource_type"] == "page":
        if clean_mode == "deep":
            discovered, partial = _discover_page_immediate_children(
                client,
                nr["resource_id"],
                root_id=nr["resource_id"],
                depth=0,
                remaining_limit=page_limit,
            )
        else:
            discovered, partial = _discover_page_shallow_children(
                client,
                nr["resource_id"],
                root_id=nr["resource_id"],
                depth=0,
                remaining_limit=page_limit,
            )
    else:
        discovered = _discover_data_source_immediate_children(
            client,
            nr["resource_id"],
            root_id=nr["resource_id"],
            depth=0,
            remaining_limit=page_limit,
        )
        partial = False
    return {
        "root_resource": nr,
        "page_size": page_limit,
        "mode": clean_mode,
        "partial": partial,
        "result_count": len(discovered),
        "results": merge_discovery_resources(discovered),
    }


# ---------------------------------------------------------------------------
# Binding operations
# ---------------------------------------------------------------------------

_VALID_RESOURCE_TYPES = {"page", "data_source"}
_VALID_SELECTION_SCOPES = {"resource", "subtree"}


def _coerce_resource_type(raw: str | None) -> str | None:
    clean = str(raw or "").strip().lower()
    if not clean:
        return None
    if clean == "database":
        clean = "data_source"
    if clean not in _VALID_RESOURCE_TYPES:
        raise LabbookError("resource_type must be 'page' or 'data_source'.")
    return clean


def _normalize_selection_scope(value: Any) -> str:
    clean = str(value or "resource").strip().lower()
    if clean not in _VALID_SELECTION_SCOPES:
        raise LabbookError("selection_scope must be 'resource' or 'subtree'.")
    return clean


def normalize_search_page_size(page_size: int | str | None = None) -> int:
    if page_size in (None, ""):
        return DEFAULT_SEARCH_PAGE_SIZE
    try:
        limit = int(page_size)
    except (TypeError, ValueError) as exc:
        raise LabbookError("page_size must be an integer number of results.") from exc
    return min(max(limit, 1), 100)


def normalize_discovery_limit(limit: int | str | None = None) -> int:
    if limit in (None, ""):
        return 50
    try:
        parsed = int(limit)
    except (TypeError, ValueError) as exc:
        raise LabbookError("limit must be an integer number of results.") from exc
    return min(max(parsed, 1), 1000)


def _slugify_alias(text: str, *, fallback: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return candidate or fallback


def _endpoint_for_resource(resource_type: str | None, resource_id: str) -> str | None:
    clean_id = normalize_notion_id(resource_id)
    return {
        "page": f"{NOTION_API_BASE}/pages/{clean_id}",
        "data_source": f"{NOTION_API_BASE}/data_sources/{clean_id}",
    }.get(resource_type or "")


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
    return {
        "resource_id": normalize_notion_id(resource_ref),
        "resource_type": _coerce_resource_type(item.get("resource_type")),
        "resource_url": str(item.get("resource_url") or "").strip() or None,
        "alias": str(item.get("alias") or "").strip() or None,
        "title": str(item.get("title") or "").strip() or None,
        "selection_scope": _normalize_selection_scope(item.get("selection_scope")),
    }


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
    for suffix in range(2, 10_001):
        candidate = f"{proposed_alias}-{suffix}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            alias_owner[candidate] = resource_id
            return candidate
    raise RuntimeError(
        f"Could not find a unique alias for {proposed_alias!r} after 10000 attempts"
    )


def _existing_bindings(project_root: Path) -> list[dict[str, Any]]:
    payload = load_project_bindings(project_root) or {}
    resources = payload.get("resources")
    if not isinstance(resources, list):
        return []
    return [item for item in resources if isinstance(item, dict)]


def _default_resource_alias(resources: list[dict[str, Any]]) -> str | None:
    if len(resources) != 1:
        return None
    return str(resources[0].get("alias") or "").strip() or None


def _sorted_bindings(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        resources,
        key=lambda i: (
            str(i.get("title") or "").lower(),
            str(i.get("resource_type") or "").lower(),
            str(i.get("resource_id") or "").lower(),
        ),
    )


def _bindings_payload(
    project_root: Path, resources: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "bindings_path": str(bindings_path(project_root)),
        "default_resource_alias": _default_resource_alias(resources),
        "resources": _sorted_bindings(resources),
    }


def _rank_search_results(
    results: list[dict[str, Any]], *, query: str | None = None
) -> list[dict[str, Any]]:
    clean_query = str(query or "").strip().lower()
    if not clean_query:
        return results

    def _rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
        title = str(item.get("title") or "").strip().lower()
        parent = item.get("parent")
        pt = (
            str(parent.get("type") or "").strip().lower()
            if isinstance(parent, dict)
            else ""
        )
        rt = str(item.get("resource_type") or "").strip().lower()
        if title == clean_query:
            tr = 0
        elif title.startswith(clean_query):
            tr = 1
        elif clean_query in title:
            tr = 2
        else:
            tr = 3
        return (
            tr,
            0 if pt == "workspace" else 1,
            {"page": 0, "data_source": 1}.get(rt, 2),
            title,
        )

    return sorted(results, key=_rank)


# ---------------------------------------------------------------------------
# High-level payload builders
# ---------------------------------------------------------------------------


def build_search_resources_payload(
    client: NotionClient,
    *,
    project_root: str,
    query: str | None = None,
    page_size: int | str | None = None,
    fetch_all: bool = False,
) -> dict[str, Any]:
    if fetch_all:
        raw_results = client.search_all(query=query)
        results = [
            n
            for item in raw_results
            if isinstance(item, dict)
            for n in [normalize_notion_resource(item)]
            if n is not None
        ]
        results = _rank_search_results(results, query=query)
        return {
            "project_root": str(project_root),
            "query": str(query or "").strip() or None,
            "page_size": len(results),
            "result_count": len(results),
            "results": results,
        }
    page_limit = normalize_search_page_size(page_size)
    raw = client.search(query=query, page_size=page_limit)
    results = [
        n
        for item in raw.get("results", [])
        if isinstance(item, dict)
        for n in [normalize_notion_resource(item)]
        if n is not None
    ]
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
    page_limit = normalize_discovery_limit(limit)
    root_resource = client.retrieve_resource(resource_id_or_url, resource_type)
    payload = discover_children_for_resource(
        client, root_resource, page_limit=page_limit, mode=str(mode or "shallow")
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
    existing = _existing_bindings(root)
    by_id: dict[str, dict[str, Any]] = {
        str(i.get("resource_id")): dict(i)
        for i in existing
        if isinstance(i.get("resource_id"), str)
    }
    for index, raw_item in enumerate(resource_refs):
        if not isinstance(raw_item, dict):
            raise LabbookError("Each item in resource_refs must be an object.")
        ni = _normalize_resource_input(raw_item)
        resource, resolved_type = client.resolve_bindable_resource(
            ni["resource_id"] or "", ni["resource_type"]
        )
        nr = normalize_notion_resource(resource)
        if nr is None:
            raise LabbookError(
                f"Resource {ni['resource_id']} is not a page or data source."
            )
        fallback_alias = (
            default_alias if len(resource_refs) == 1 and default_alias else None
        )
        previous = by_id.get(nr["resource_id"])
        by_id[nr["resource_id"]] = _normalize_binding_entry(
            resource_id=nr["resource_id"],
            resource_type=resolved_type or nr["resource_type"],
            resource_url=ni["resource_url"] or nr["resource_url"],
            title=ni["title"] or nr["title"],
            alias=ni["alias"]
            or fallback_alias
            or (previous or {}).get("alias")
            or f"resource-{index + 1}",
            selection_scope=ni["selection_scope"],
            source="manual_bind",
            bound_at=(previous or {}).get("bound_at"),
        )
    final = _sorted_bindings(list(by_id.values()))
    used_aliases: set[str] = set()
    alias_owner: dict[str, str] = {}
    for r in final:
        r["alias"] = _alias_for_resource(
            resource_id=str(r["resource_id"]),
            proposed_alias=_slugify_alias(
                str(r["alias"]), fallback=str(r["resource_type"])
            ),
            used_aliases=used_aliases,
            alias_owner=alias_owner,
        )
    payload = _bindings_payload(root, final)
    save_project_bindings(root, payload)
    return {
        "project_root": str(root),
        "bindings_path": payload["bindings_path"],
        "default_resource_alias": payload["default_resource_alias"],
        "resource_count": len(final),
        "resources": final,
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
    root = resolve_project_root(project_root)
    payload = load_project_bindings(root) or _bindings_payload(root, [])
    resources = payload.get("resources")
    if not isinstance(resources, list):
        resources = []
    return {
        "project_root": str(root),
        "bindings_path": payload.get("bindings_path") or str(bindings_path(root)),
        "default_resource_alias": payload.get("default_resource_alias"),
        "resource_count": len(resources),
        "resources": resources,
    }


def project_bindings_resource_text(project_root: str | Path | None = None) -> str:
    return json.dumps(
        build_list_bindings_payload(project_root),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# High-level convenience functions
# ---------------------------------------------------------------------------


def _resolve_client(
    client: NotionClient | None, project_root: str | Path | None
) -> tuple[NotionClient, str]:
    if client is None:
        from .auth import notion_client_for_project

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
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_search_resources_payload(
        resolved_client, project_root=root_str, query=query, page_size=page_size
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
    resolved_client, root_str = _resolve_client(client, project_root)
    return build_bind_resources_payload(
        resolved_client,
        project_root=root_str,
        resource_refs=resource_refs,
        default_alias=default_alias,
    )


def list_bindings(project_root: str | Path | None = None) -> dict[str, Any]:
    return build_list_bindings_payload(project_root)
