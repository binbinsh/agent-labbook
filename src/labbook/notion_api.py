from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib import error, parse, request

from .state import DEFAULT_NOTION_VERSION, LabbookError, normalize_notion_id

logger = logging.getLogger("labbook.notion_api")


NOTION_API_BASE = "https://api.notion.com/v1"
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 3
_INITIAL_BACKOFF_SECONDS = 1.0
_BACKOFF_MULTIPLIER = 2.0


class NotionApiError(LabbookError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _decode_payload(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NotionApiError("Notion API returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise NotionApiError("Notion API returned an unexpected payload.")
    return decoded


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value into seconds, or return None."""
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except (TypeError, ValueError):
        return None


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
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{NOTION_API_BASE}{path}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        last_exc: Exception | None = None
        backoff = _INITIAL_BACKOFF_SECONDS

        for attempt in range(_MAX_RETRIES + 1):
            req = request.Request(
                url, data=data, headers=self._headers(), method=method.upper()
            )
            try:
                logger.debug(
                    "Notion API %s %s (attempt %d)", method.upper(), url, attempt + 1
                )
                with request.urlopen(req, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8")
                return _decode_payload(payload)
            except error.HTTPError as exc:
                logger.warning(
                    "Notion API %s %s returned HTTP %d", method.upper(), url, exc.code
                )
                raw = exc.read().decode("utf-8", errors="replace")

                if exc.code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                    wait = retry_after if retry_after is not None else backoff
                    logger.info(
                        "Retrying %s %s in %.1fs (HTTP %d, attempt %d/%d)",
                        method.upper(),
                        url,
                        wait,
                        exc.code,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                    )
                    time.sleep(wait)
                    backoff *= _BACKOFF_MULTIPLIER
                    last_exc = exc
                    continue

                try:
                    parsed_error = json.loads(raw)
                except json.JSONDecodeError:
                    parsed_error = {"message": raw}
                message = parsed_error.get("message") or str(exc)
                raise NotionApiError(
                    f"Notion API {exc.code}: {message}", status_code=exc.code
                ) from exc
            except error.URLError as exc:
                if attempt < _MAX_RETRIES:
                    logger.info(
                        "Retrying %s %s in %.1fs (URLError: %s, attempt %d/%d)",
                        method.upper(),
                        url,
                        backoff,
                        exc.reason,
                        attempt + 1,
                        _MAX_RETRIES + 1,
                    )
                    time.sleep(backoff)
                    backoff *= _BACKOFF_MULTIPLIER
                    last_exc = exc
                    continue
                logger.error("Notion API unreachable: %s", exc.reason)
                raise NotionApiError(
                    f"Could not reach Notion API: {exc.reason}"
                ) from exc

        # Should not be reached, but handle defensively
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
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "page_size": page_size,
            "sort": {
                "direction": "descending",
                "timestamp": "last_edited_time",
            },
        }
        clean_query = str(query or "").strip()
        if clean_query:
            body["query"] = clean_query
        return self._request("POST", "/search", body=body)

    def list_block_children(
        self,
        block_id: str,
        *,
        page_size: int = 100,
        start_cursor: str | None = None,
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
        normalized_type = str(resource_type or "").strip().lower()
        normalized_id = normalize_notion_id(resource_id)

        if normalized_type == "page":
            return self.retrieve_page(normalized_id)
        if normalized_type in {"data_source", "database"}:
            return self.retrieve_data_source(normalized_id)

        try:
            return self.retrieve_page(normalized_id)
        except NotionApiError as exc:
            if exc.status_code not in {400, 404}:
                raise
        return self.retrieve_data_source(normalized_id)
