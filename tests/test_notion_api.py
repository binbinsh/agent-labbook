"""Tests for notion_api — HTTP client, retry logic, error handling."""

from __future__ import annotations

import json
import unittest
from http.client import HTTPResponse
from io import BytesIO
from typing import Any
from unittest import mock
from urllib import error

from labbook.notion_api import (
    NOTION_API_BASE,
    NotionApiError,
    NotionClient,
    _decode_payload,
    _parse_retry_after,
)


class DecodePayloadTests(unittest.TestCase):
    def test_valid_json(self) -> None:
        result = _decode_payload('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_empty_string(self) -> None:
        result = _decode_payload("")
        self.assertEqual(result, {})

    def test_whitespace_only(self) -> None:
        result = _decode_payload("   ")
        self.assertEqual(result, {})

    def test_invalid_json(self) -> None:
        with self.assertRaises(NotionApiError) as ctx:
            _decode_payload("not json")
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_non_dict_json(self) -> None:
        with self.assertRaises(NotionApiError) as ctx:
            _decode_payload("[1, 2, 3]")
        self.assertIn("unexpected payload", str(ctx.exception))


class ParseRetryAfterTests(unittest.TestCase):
    def test_valid_integer(self) -> None:
        self.assertEqual(_parse_retry_after("5"), 5.0)

    def test_valid_float(self) -> None:
        self.assertEqual(_parse_retry_after("1.5"), 1.5)

    def test_negative_clamped_to_zero(self) -> None:
        self.assertEqual(_parse_retry_after("-3"), 0.0)

    def test_none_input(self) -> None:
        self.assertIsNone(_parse_retry_after(None))

    def test_empty_string(self) -> None:
        self.assertIsNone(_parse_retry_after(""))

    def test_non_numeric(self) -> None:
        self.assertIsNone(_parse_retry_after("Thu, 01 Jan 2026 00:00:00 GMT"))


class NotionClientInitTests(unittest.TestCase):
    def test_valid_token(self) -> None:
        client = NotionClient(token="secret_test_token")
        self.assertEqual(client.token, "secret_test_token")

    def test_empty_token_raises(self) -> None:
        with self.assertRaises(NotionApiError) as ctx:
            NotionClient(token="")
        self.assertIn("Missing Notion integration secret", str(ctx.exception))

    def test_whitespace_token_raises(self) -> None:
        with self.assertRaises(NotionApiError) as ctx:
            NotionClient(token="   ")
        self.assertIn("Missing Notion integration secret", str(ctx.exception))

    def test_token_stripped(self) -> None:
        client = NotionClient(token="  secret_test_token  ")
        self.assertEqual(client.token, "secret_test_token")


def _make_http_error(
    code: int,
    body: str = "",
    headers: dict[str, str] | None = None,
) -> error.HTTPError:
    """Build a fake HTTPError with optional headers."""
    fp = BytesIO(body.encode("utf-8"))
    resp_headers = mock.Mock()
    resp_headers.get = lambda key, default=None: (headers or {}).get(key, default)
    return error.HTTPError(
        url=f"{NOTION_API_BASE}/test",
        code=code,
        msg=f"HTTP {code}",
        hdrs=resp_headers,
        fp=fp,
    )


class NotionClientRequestTests(unittest.TestCase):
    """Test the HTTP dispatch + retry logic in NotionClient._request."""

    def _client(self) -> NotionClient:
        return NotionClient(token="secret_test_token", timeout=1.0)

    @mock.patch("labbook.notion_api.request.urlopen")
    def test_successful_get(self, urlopen_mock: mock.Mock) -> None:
        response = mock.Mock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        urlopen_mock.return_value = response

        result = self._client()._request("GET", "/test")
        self.assertEqual(result, {"ok": True})

    @mock.patch("labbook.notion_api.request.urlopen")
    def test_successful_post_with_body(self, urlopen_mock: mock.Mock) -> None:
        response = mock.Mock()
        response.read.return_value = b'{"id": "123"}'
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        urlopen_mock.return_value = response

        result = self._client()._request("POST", "/test", body={"key": "value"})
        self.assertEqual(result, {"id": "123"})

        # Verify the request was made with JSON body
        called_request = urlopen_mock.call_args[0][0]
        self.assertEqual(json.loads(called_request.data), {"key": "value"})

    @mock.patch("labbook.notion_api.request.urlopen")
    def test_non_retryable_http_error(self, urlopen_mock: mock.Mock) -> None:
        urlopen_mock.side_effect = _make_http_error(401, '{"message": "Unauthorized"}')

        with self.assertRaises(NotionApiError) as ctx:
            self._client()._request("GET", "/test")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Unauthorized", str(ctx.exception))

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_retries_on_429(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        """429 should be retried, and the successful response returned."""
        success_response = mock.Mock()
        success_response.read.return_value = b'{"ok": true}'
        success_response.__enter__ = mock.Mock(return_value=success_response)
        success_response.__exit__ = mock.Mock(return_value=False)

        urlopen_mock.side_effect = [
            _make_http_error(429, "{}"),
            success_response,
        ]

        result = self._client()._request("GET", "/test")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once()

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_respects_retry_after_header(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        success_response = mock.Mock()
        success_response.read.return_value = b'{"ok": true}'
        success_response.__enter__ = mock.Mock(return_value=success_response)
        success_response.__exit__ = mock.Mock(return_value=False)

        urlopen_mock.side_effect = [
            _make_http_error(429, "{}", headers={"Retry-After": "3"}),
            success_response,
        ]

        self._client()._request("GET", "/test")
        sleep_mock.assert_called_once_with(3.0)

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_retries_on_502(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        success_response = mock.Mock()
        success_response.read.return_value = b'{"ok": true}'
        success_response.__enter__ = mock.Mock(return_value=success_response)
        success_response.__exit__ = mock.Mock(return_value=False)

        urlopen_mock.side_effect = [
            _make_http_error(502, "{}"),
            success_response,
        ]

        result = self._client()._request("GET", "/test")
        self.assertEqual(result, {"ok": True})

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_max_retries_exceeded(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        urlopen_mock.side_effect = [
            _make_http_error(429, "{}"),
            _make_http_error(429, "{}"),
            _make_http_error(429, "{}"),
            _make_http_error(429, "{}"),
        ]

        with self.assertRaises(NotionApiError) as ctx:
            self._client()._request("GET", "/test")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(urlopen_mock.call_count, 4)  # 1 initial + 3 retries

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_retries_on_url_error(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        success_response = mock.Mock()
        success_response.read.return_value = b'{"ok": true}'
        success_response.__enter__ = mock.Mock(return_value=success_response)
        success_response.__exit__ = mock.Mock(return_value=False)

        urlopen_mock.side_effect = [
            error.URLError("Connection refused"),
            success_response,
        ]

        result = self._client()._request("GET", "/test")
        self.assertEqual(result, {"ok": True})
        sleep_mock.assert_called_once()

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_url_error_exhausts_retries(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        urlopen_mock.side_effect = error.URLError("Connection refused")

        with self.assertRaises(NotionApiError) as ctx:
            self._client()._request("GET", "/test")
        self.assertIn("Could not reach Notion API", str(ctx.exception))

    @mock.patch("labbook.notion_api.time.sleep")
    @mock.patch("labbook.notion_api.request.urlopen")
    def test_exponential_backoff(
        self, urlopen_mock: mock.Mock, sleep_mock: mock.Mock
    ) -> None:
        urlopen_mock.side_effect = [
            _make_http_error(503, "{}"),
            _make_http_error(503, "{}"),
            _make_http_error(503, "{}"),
            _make_http_error(503, "{}"),
        ]

        with self.assertRaises(NotionApiError):
            self._client()._request("GET", "/test")

        # Backoff: 1.0, 2.0, 4.0 (3 sleeps for 3 retries)
        sleep_calls = [call[0][0] for call in sleep_mock.call_args_list]
        self.assertEqual(sleep_calls, [1.0, 2.0, 4.0])


class NotionClientRetrieveResourceTests(unittest.TestCase):
    def test_page_type_calls_retrieve_page(self) -> None:
        client = NotionClient(token="secret_test_token")
        with mock.patch.object(
            client, "retrieve_page", return_value={"object": "page", "id": "abc"}
        ) as mock_page:
            result = client.retrieve_resource(
                "01234567-89ab-cdef-0123-456789abcdef", "page"
            )
        mock_page.assert_called_once()
        self.assertEqual(result["object"], "page")

    def test_data_source_type_calls_retrieve_data_source(self) -> None:
        client = NotionClient(token="secret_test_token")
        with mock.patch.object(
            client,
            "retrieve_data_source",
            return_value={"object": "data_source", "id": "abc"},
        ) as mock_ds:
            result = client.retrieve_resource(
                "01234567-89ab-cdef-0123-456789abcdef", "data_source"
            )
        mock_ds.assert_called_once()
        self.assertEqual(result["object"], "data_source")

    def test_database_type_calls_retrieve_data_source(self) -> None:
        client = NotionClient(token="secret_test_token")
        with mock.patch.object(
            client,
            "retrieve_data_source",
            return_value={"object": "data_source", "id": "abc"},
        ) as mock_ds:
            result = client.retrieve_resource(
                "01234567-89ab-cdef-0123-456789abcdef", "database"
            )
        mock_ds.assert_called_once()

    def test_unknown_type_tries_page_then_data_source(self) -> None:
        client = NotionClient(token="secret_test_token")
        with mock.patch.object(
            client,
            "retrieve_page",
            side_effect=NotionApiError("Not found", status_code=404),
        ):
            with mock.patch.object(
                client,
                "retrieve_data_source",
                return_value={"object": "data_source", "id": "abc"},
            ) as mock_ds:
                result = client.retrieve_resource(
                    "01234567-89ab-cdef-0123-456789abcdef"
                )
        mock_ds.assert_called_once()
        self.assertEqual(result["object"], "data_source")

    def test_unknown_type_page_non_404_error_propagates(self) -> None:
        client = NotionClient(token="secret_test_token")
        with mock.patch.object(
            client,
            "retrieve_page",
            side_effect=NotionApiError("Server error", status_code=500),
        ):
            with self.assertRaises(NotionApiError) as ctx:
                client.retrieve_resource("01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
