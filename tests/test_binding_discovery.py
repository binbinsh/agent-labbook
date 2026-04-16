"""Tests for binding_discovery — title extraction, type normalization, merge, etc."""

from __future__ import annotations

import unittest
from unittest import mock

from labbook.notion import (
    _normalize_parent_metadata,
    merge_discovery_resources,
    normalize_notion_resource,
    resource_title,
    resource_type_of,
)


class ResourceTitleTests(unittest.TestCase):
    def test_page_with_top_level_title(self) -> None:
        resource = {
            "object": "page",
            "title": [{"plain_text": "My Page"}],
        }
        self.assertEqual(resource_title(resource), "My Page")

    def test_page_with_properties_title(self) -> None:
        resource = {
            "object": "page",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "From Properties"}],
                }
            },
        }
        self.assertEqual(resource_title(resource), "From Properties")

    def test_database_with_title(self) -> None:
        resource = {
            "object": "database",
            "title": [{"plain_text": "My Database"}],
        }
        self.assertEqual(resource_title(resource), "My Database")

    def test_data_source_with_title(self) -> None:
        resource = {
            "object": "data_source",
            "title": [{"plain_text": "My Data Source"}],
        }
        self.assertEqual(resource_title(resource), "My Data Source")

    def test_fallback_to_name_field(self) -> None:
        resource = {"object": "unknown_type", "name": "Named Resource"}
        self.assertEqual(resource_title(resource), "Named Resource")

    def test_returns_none_for_empty_resource(self) -> None:
        resource = {"object": "page"}
        self.assertIsNone(resource_title(resource))

    def test_empty_title_list(self) -> None:
        resource = {"object": "page", "title": []}
        self.assertIsNone(resource_title(resource))

    def test_rich_text_concatenation(self) -> None:
        resource = {
            "object": "page",
            "title": [
                {"plain_text": "Hello "},
                {"plain_text": "World"},
            ],
        }
        self.assertEqual(resource_title(resource), "Hello World")

    def test_non_list_title_ignored(self) -> None:
        resource = {"object": "page", "title": "Not a list"}
        self.assertIsNone(resource_title(resource))


class ResourceTypeOfTests(unittest.TestCase):
    def test_page(self) -> None:
        self.assertEqual(resource_type_of({"object": "page"}), "page")

    def test_database_normalized_to_data_source(self) -> None:
        self.assertEqual(resource_type_of({"object": "database"}), "data_source")

    def test_data_source(self) -> None:
        self.assertEqual(resource_type_of({"object": "data_source"}), "data_source")

    def test_unknown_type(self) -> None:
        self.assertEqual(resource_type_of({"object": "block"}), "unknown")

    def test_case_insensitive(self) -> None:
        self.assertEqual(resource_type_of({"object": "PAGE"}), "page")
        self.assertEqual(resource_type_of({"object": "Database"}), "data_source")

    def test_falls_back_to_resource_type_field(self) -> None:
        self.assertEqual(resource_type_of({"resource_type": "page"}), "page")

    def test_empty_object(self) -> None:
        self.assertEqual(resource_type_of({}), "unknown")


class NormalizeNotionResourceTests(unittest.TestCase):
    def test_normalizes_page(self) -> None:
        resource = {
            "object": "page",
            "id": "01234567-89ab-cdef-0123-456789abcdef",
            "url": "https://www.notion.so/example",
            "title": [{"plain_text": "Test Page"}],
            "last_edited_time": "2026-04-10T00:00:00.000Z",
            "parent": {"type": "workspace"},
        }
        result = normalize_notion_resource(resource)
        self.assertIsNotNone(result)
        self.assertEqual(result["resource_type"], "page")
        self.assertEqual(result["title"], "Test Page")
        self.assertEqual(result["resource_id"], "01234567-89ab-cdef-0123-456789abcdef")

    def test_normalizes_database_to_data_source(self) -> None:
        resource = {
            "object": "database",
            "id": "fedcba98-7654-3210-fedc-ba9876543210",
            "url": "https://www.notion.so/db",
            "title": [{"plain_text": "Specs DB"}],
        }
        result = normalize_notion_resource(resource)
        self.assertIsNotNone(result)
        self.assertEqual(result["resource_type"], "data_source")
        self.assertEqual(result["title"], "Specs DB")

    def test_returns_none_for_unknown_type(self) -> None:
        resource = {
            "object": "block",
            "id": "01234567-89ab-cdef-0123-456789abcdef",
        }
        result = normalize_notion_resource(resource)
        self.assertIsNone(result)

    def test_uses_resource_id_as_fallback_title(self) -> None:
        resource = {
            "object": "page",
            "id": "01234567-89ab-cdef-0123-456789abcdef",
        }
        result = normalize_notion_resource(resource)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "01234567-89ab-cdef-0123-456789abcdef")


class NormalizeParentMetadataTests(unittest.TestCase):
    def test_workspace_parent(self) -> None:
        result = _normalize_parent_metadata({"type": "workspace"})
        self.assertEqual(result["parent_type"], "workspace")
        self.assertIsNone(result["parent_id"])
        self.assertIsNone(result["parent_database_id"])

    def test_page_parent(self) -> None:
        result = _normalize_parent_metadata(
            {
                "type": "page_id",
                "page_id": "01234567-89ab-cdef-0123-456789abcdef",
            }
        )
        self.assertEqual(result["parent_type"], "page_id")
        self.assertEqual(result["parent_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertIsNone(result["parent_database_id"])

    def test_data_source_parent(self) -> None:
        result = _normalize_parent_metadata(
            {
                "type": "data_source_id",
                "data_source_id": "01234567-89ab-cdef-0123-456789abcdef",
                "database_id": "fedcba98-7654-3210-fedc-ba9876543210",
            }
        )
        self.assertEqual(result["parent_type"], "data_source_id")
        self.assertEqual(result["parent_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(
            result["parent_database_id"], "fedcba98-7654-3210-fedc-ba9876543210"
        )

    def test_database_parent(self) -> None:
        result = _normalize_parent_metadata(
            {
                "type": "database_id",
                "database_id": "01234567-89ab-cdef-0123-456789abcdef",
            }
        )
        self.assertEqual(result["parent_type"], "database_id")
        self.assertEqual(result["parent_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(
            result["parent_database_id"], "01234567-89ab-cdef-0123-456789abcdef"
        )

    def test_block_parent(self) -> None:
        result = _normalize_parent_metadata(
            {
                "type": "block_id",
                "block_id": "01234567-89ab-cdef-0123-456789abcdef",
            }
        )
        self.assertEqual(result["parent_type"], "block_id")
        self.assertEqual(result["parent_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertIsNone(result["parent_database_id"])

    def test_none_parent(self) -> None:
        result = _normalize_parent_metadata(None)
        self.assertIsNone(result["parent_type"])
        self.assertIsNone(result["parent_id"])
        self.assertIsNone(result["parent_database_id"])

    def test_unknown_parent_type(self) -> None:
        result = _normalize_parent_metadata({"type": "some_new_type"})
        self.assertEqual(result["parent_type"], "some_new_type")
        self.assertIsNone(result["parent_id"])
        self.assertIsNone(result["parent_database_id"])


class MergeDiscoveryResourcesTests(unittest.TestCase):
    def test_deduplicates_by_resource_id(self) -> None:
        list1 = [{"resource_id": "aaa", "title": "First", "resource_type": "page"}]
        list2 = [{"resource_id": "aaa", "title": "Updated", "resource_type": "page"}]
        result = merge_discovery_resources(list1, list2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Updated")

    def test_sorts_pages_before_data_sources(self) -> None:
        resources = [
            {"resource_id": "bbb", "title": "B", "resource_type": "data_source"},
            {"resource_id": "aaa", "title": "A", "resource_type": "page"},
        ]
        result = merge_discovery_resources(resources)
        self.assertEqual(result[0]["resource_type"], "page")
        self.assertEqual(result[1]["resource_type"], "data_source")

    def test_sorts_by_title_within_type(self) -> None:
        resources = [
            {"resource_id": "bbb", "title": "Zebra", "resource_type": "page"},
            {"resource_id": "aaa", "title": "Apple", "resource_type": "page"},
        ]
        result = merge_discovery_resources(resources)
        self.assertEqual(result[0]["title"], "Apple")
        self.assertEqual(result[1]["title"], "Zebra")

    def test_skips_non_dict_items(self) -> None:
        resources = [
            {"resource_id": "aaa", "title": "A", "resource_type": "page"},
            "not a dict",
            42,
        ]
        result = merge_discovery_resources(resources)
        self.assertEqual(len(result), 1)

    def test_skips_items_without_resource_id(self) -> None:
        resources = [
            {"title": "No ID", "resource_type": "page"},
            {"resource_id": "aaa", "title": "Has ID", "resource_type": "page"},
        ]
        result = merge_discovery_resources(resources)
        self.assertEqual(len(result), 1)

    def test_empty_input(self) -> None:
        result = merge_discovery_resources([], [])
        self.assertEqual(result, [])

    def test_multiple_lists(self) -> None:
        l1 = [{"resource_id": "aaa", "title": "A", "resource_type": "page"}]
        l2 = [{"resource_id": "bbb", "title": "B", "resource_type": "page"}]
        l3 = [{"resource_id": "ccc", "title": "C", "resource_type": "data_source"}]
        result = merge_discovery_resources(l1, l2, l3)
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
