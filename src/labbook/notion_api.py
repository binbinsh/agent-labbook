from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from .state import DEFAULT_NOTION_VERSION, LabbookError, normalize_notion_id


NOTION_API_BASE = "https://api.notion.com/v1"


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
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{NOTION_API_BASE}{path}"
        if query:
            encoded = parse.urlencode(
                {key: value for key, value in query.items() if value is not None},
                doseq=True,
            )
            if encoded:
                url = f"{url}?{encoded}"

        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = request.Request(url, data=data, headers=self._headers(), method=method.upper())
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed_error = json.loads(raw)
            except json.JSONDecodeError:
                parsed_error = {"message": raw}
            message = parsed_error.get("message") or str(exc)
            raise NotionApiError(f"Notion API {exc.code}: {message}", status_code=exc.code) from exc
        except error.URLError as exc:
            raise NotionApiError(f"Could not reach Notion API: {exc.reason}") from exc

        return _decode_payload(payload)

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/users/me")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{normalize_notion_id(page_id)}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._request("GET", f"/data_sources/{normalize_notion_id(data_source_id)}")

    def search(self, *, page_size: int = 50) -> dict[str, Any]:
        return self._request(
            "POST",
            "/search",
            body={
                "page_size": page_size,
                "sort": {
                    "direction": "descending",
                    "timestamp": "last_edited_time",
                },
            },
        )

    def retrieve_resource(self, resource_id: str, resource_type: str | None = None) -> dict[str, Any]:
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
