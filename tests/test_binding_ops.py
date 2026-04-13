"""Tests for binding_ops — ranking, normalization, alias, validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from labbook.binding_ops import (
    DEFAULT_SEARCH_PAGE_SIZE,
    MAX_SEARCH_PAGE_SIZE,
    MIN_SEARCH_PAGE_SIZE,
    DEFAULT_DISCOVERY_LIMIT,
    MAX_DISCOVERY_LIMIT,
    MIN_DISCOVERY_LIMIT,
    _alias_for_resource,
    _endpoint_for_resource,
    _normalize_binding_entry,
    _normalize_resource_input,
    _normalize_selection_scope,
    _rank_search_results,
    _slugify_alias,
    _MAX_ALIAS_SUFFIX,
    normalize_discovery_limit,
    normalize_search_page_size,
    build_list_bindings_payload,
)
from labbook.notion_api import NOTION_API_BASE
from labbook.state import LabbookError


class NormalizeSearchPageSizeTests(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(normalize_search_page_size(None), DEFAULT_SEARCH_PAGE_SIZE)

    def test_empty_string(self) -> None:
        self.assertEqual(normalize_search_page_size(""), DEFAULT_SEARCH_PAGE_SIZE)

    def test_valid_integer(self) -> None:
        self.assertEqual(normalize_search_page_size(50), 50)

    def test_string_integer(self) -> None:
        self.assertEqual(normalize_search_page_size("50"), 50)

    def test_clamp_below_min(self) -> None:
        self.assertEqual(normalize_search_page_size(-5), MIN_SEARCH_PAGE_SIZE)

    def test_clamp_above_max(self) -> None:
        self.assertEqual(normalize_search_page_size(500), MAX_SEARCH_PAGE_SIZE)

    def test_non_integer_raises(self) -> None:
        with self.assertRaises(LabbookError):
            normalize_search_page_size("not_a_number")


class NormalizeDiscoveryLimitTests(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(normalize_discovery_limit(None), DEFAULT_DISCOVERY_LIMIT)

    def test_clamp_below_min(self) -> None:
        self.assertEqual(normalize_discovery_limit(0), MIN_DISCOVERY_LIMIT)

    def test_clamp_above_max(self) -> None:
        self.assertEqual(normalize_discovery_limit(999), MAX_DISCOVERY_LIMIT)

    def test_non_integer_raises(self) -> None:
        with self.assertRaises(LabbookError):
            normalize_discovery_limit("abc")


class EndpointForResourceTests(unittest.TestCase):
    def test_page(self) -> None:
        result = _endpoint_for_resource("page", "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(
            result,
            f"{NOTION_API_BASE}/pages/01234567-89ab-cdef-0123-456789abcdef",
        )

    def test_data_source(self) -> None:
        result = _endpoint_for_resource(
            "data_source", "01234567-89ab-cdef-0123-456789abcdef"
        )
        self.assertEqual(
            result,
            f"{NOTION_API_BASE}/data_sources/01234567-89ab-cdef-0123-456789abcdef",
        )

    def test_unknown_returns_none(self) -> None:
        result = _endpoint_for_resource("block", "01234567-89ab-cdef-0123-456789abcdef")
        self.assertIsNone(result)


class SlugifyAliasTests(unittest.TestCase):
    def test_simple_text(self) -> None:
        self.assertEqual(
            _slugify_alias("Project Home", fallback="resource"), "project-home"
        )

    def test_special_characters(self) -> None:
        self.assertEqual(_slugify_alias("My Page!@#$%", fallback="resource"), "my-page")

    def test_empty_returns_fallback(self) -> None:
        self.assertEqual(_slugify_alias("", fallback="fallback"), "fallback")

    def test_unicode_stripped(self) -> None:
        result = _slugify_alias("工作区", fallback="resource")
        self.assertEqual(result, "resource")  # Non-ASCII stripped to empty -> fallback


class NormalizeSelectionScopeTests(unittest.TestCase):
    def test_resource(self) -> None:
        self.assertEqual(_normalize_selection_scope("resource"), "resource")

    def test_subtree(self) -> None:
        self.assertEqual(_normalize_selection_scope("subtree"), "subtree")

    def test_default_is_resource(self) -> None:
        self.assertEqual(_normalize_selection_scope(None), "resource")

    def test_invalid_raises(self) -> None:
        with self.assertRaises(LabbookError):
            _normalize_selection_scope("invalid")


class NormalizeBindingEntryTests(unittest.TestCase):
    def test_valid_page(self) -> None:
        entry = _normalize_binding_entry(
            resource_id="01234567-89ab-cdef-0123-456789abcdef",
            resource_type="page",
            resource_url="https://www.notion.so/example",
            title="Test Page",
            alias="test-page",
        )
        self.assertEqual(entry["resource_type"], "page")
        self.assertEqual(entry["title"], "Test Page")
        self.assertEqual(entry["alias"], "test-page")
        self.assertEqual(entry["selection_scope"], "resource")

    def test_database_coerced_to_data_source(self) -> None:
        entry = _normalize_binding_entry(
            resource_id="01234567-89ab-cdef-0123-456789abcdef",
            resource_type="database",
            resource_url=None,
            title="My DB",
            alias="my-db",
        )
        self.assertEqual(entry["resource_type"], "data_source")

    def test_invalid_type_raises(self) -> None:
        with self.assertRaises(LabbookError):
            _normalize_binding_entry(
                resource_id="01234567-89ab-cdef-0123-456789abcdef",
                resource_type="block",
                resource_url=None,
                title="Bad",
                alias="bad",
            )

    def test_url_fallback_when_none(self) -> None:
        entry = _normalize_binding_entry(
            resource_id="01234567-89ab-cdef-0123-456789abcdef",
            resource_type="page",
            resource_url=None,
            title="Test",
            alias="test",
        )
        self.assertIn("/pages/", entry["resource_url"])


class AliasForResourceTests(unittest.TestCase):
    def test_unique_alias_accepted(self) -> None:
        used: set[str] = set()
        owner: dict[str, str] = {}
        result = _alias_for_resource(
            resource_id="aaa",
            proposed_alias="my-page",
            used_aliases=used,
            alias_owner=owner,
        )
        self.assertEqual(result, "my-page")

    def test_duplicate_alias_gets_suffix(self) -> None:
        used: set[str] = {"my-page"}
        owner: dict[str, str] = {"my-page": "other-id"}
        result = _alias_for_resource(
            resource_id="aaa",
            proposed_alias="my-page",
            used_aliases=used,
            alias_owner=owner,
        )
        self.assertEqual(result, "my-page-2")

    def test_same_resource_keeps_alias(self) -> None:
        used: set[str] = {"my-page"}
        owner: dict[str, str] = {"my-page": "aaa"}
        result = _alias_for_resource(
            resource_id="aaa",
            proposed_alias="my-page",
            used_aliases=used,
            alias_owner=owner,
        )
        self.assertEqual(result, "my-page")  # Same owner — no suffix

    def test_multiple_collisions(self) -> None:
        used: set[str] = {"my-page", "my-page-2", "my-page-3"}
        owner: dict[str, str] = {
            "my-page": "id-1",
            "my-page-2": "id-2",
            "my-page-3": "id-3",
        }
        result = _alias_for_resource(
            resource_id="id-new",
            proposed_alias="my-page",
            used_aliases=used,
            alias_owner=owner,
        )
        self.assertEqual(result, "my-page-4")


class RankSearchResultsTests(unittest.TestCase):
    def test_empty_query_returns_original_order(self) -> None:
        results = [
            {"title": "Zebra", "resource_type": "page"},
            {"title": "Apple", "resource_type": "page"},
        ]
        ranked = _rank_search_results(results, query=None)
        self.assertEqual(ranked[0]["title"], "Zebra")

    def test_exact_match_first(self) -> None:
        results = [
            {
                "title": "Project Docs",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
            {
                "title": "Project",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
            {
                "title": "My Project Page",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
        ]
        ranked = _rank_search_results(results, query="Project")
        self.assertEqual(ranked[0]["title"], "Project")

    def test_prefix_before_contains(self) -> None:
        results = [
            {
                "title": "My Project Page",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
            {
                "title": "Project Docs",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
        ]
        ranked = _rank_search_results(results, query="Project")
        self.assertEqual(ranked[0]["title"], "Project Docs")  # prefix match
        self.assertEqual(ranked[1]["title"], "My Project Page")  # contains match

    def test_root_like_items_ranked_higher(self) -> None:
        results = [
            {
                "title": "Project",
                "resource_type": "page",
                "parent": {"type": "page_id", "page_id": "abc"},
            },
            {
                "title": "Project",
                "resource_type": "page",
                "parent": {"type": "workspace"},
            },
        ]
        ranked = _rank_search_results(results, query="Project")
        self.assertEqual(ranked[0]["parent"]["type"], "workspace")


class NormalizeResourceInputTests(unittest.TestCase):
    def test_valid_resource_id(self) -> None:
        result = _normalize_resource_input(
            {
                "resource_id_or_url": "01234567-89ab-cdef-0123-456789abcdef",
                "resource_type": "page",
                "selection_scope": "subtree",
            }
        )
        self.assertEqual(result["resource_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(result["resource_type"], "page")
        self.assertEqual(result["selection_scope"], "subtree")

    def test_database_coerced_to_data_source(self) -> None:
        result = _normalize_resource_input(
            {
                "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                "resource_type": "database",
            }
        )
        self.assertEqual(result["resource_type"], "data_source")

    def test_missing_id_raises(self) -> None:
        with self.assertRaises(LabbookError):
            _normalize_resource_input({"resource_type": "page"})

    def test_invalid_resource_type_raises(self) -> None:
        with self.assertRaises(LabbookError):
            _normalize_resource_input(
                {
                    "resource_id": "01234567-89ab-cdef-0123-456789abcdef",
                    "resource_type": "block",
                }
            )


class BuildListBindingsPayloadTests(unittest.TestCase):
    def test_empty_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_list_bindings_payload(tmpdir)
        self.assertEqual(result["resource_count"], 0)
        self.assertEqual(result["resources"], [])
        self.assertIsNone(result["default_resource_alias"])


if __name__ == "__main__":
    unittest.main()
