"""Tests for state — normalize_notion_id, resolve_project_root, edge cases."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from labbook.state import (
    LabbookError,
    normalize_notion_id,
    resolve_project_root,
    clear_project_session,
    clear_project_bindings,
    save_project_session,
    load_project_session,
    _load_json,
    session_path,
)


class NormalizeNotionIdTests(unittest.TestCase):
    def test_hyphenated_uuid(self) -> None:
        result = normalize_notion_id("01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(result, "01234567-89ab-cdef-0123-456789abcdef")

    def test_unhyphenated_uuid(self) -> None:
        result = normalize_notion_id("0123456789abcdef0123456789abcdef")
        self.assertEqual(result, "01234567-89ab-cdef-0123-456789abcdef")

    def test_uuid_from_url(self) -> None:
        result = normalize_notion_id(
            "https://www.notion.so/workspace/My-Page-0123456789abcdef0123456789abcdef"
        )
        self.assertEqual(result, "01234567-89ab-cdef-0123-456789abcdef")

    def test_multiple_uuids_in_url_takes_last(self) -> None:
        # URLs may have workspace IDs and page IDs; we take the last one
        result = normalize_notion_id(
            "https://www.notion.so/aabbccdd-eeff-0011-2233-445566778899/Page-0123456789abcdef0123456789abcdef"
        )
        self.assertEqual(result, "01234567-89ab-cdef-0123-456789abcdef")

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(LabbookError) as ctx:
            normalize_notion_id("")
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_none_raises(self) -> None:
        with self.assertRaises(LabbookError) as ctx:
            normalize_notion_id(None)
        self.assertIn("cannot be empty", str(ctx.exception))

    def test_no_uuid_found_raises(self) -> None:
        with self.assertRaises(LabbookError) as ctx:
            normalize_notion_id("not-a-uuid")
        self.assertIn("Could not find", str(ctx.exception))

    def test_uppercase_hex(self) -> None:
        result = normalize_notion_id("0123456789ABCDEF0123456789ABCDEF")
        self.assertEqual(result, "01234567-89ab-cdef-0123-456789abcdef")


class ResolveProjectRootTests(unittest.TestCase):
    def test_valid_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_project_root(tmpdir)
            self.assertEqual(result, Path(tmpdir).resolve())

    def test_none_defaults_to_cwd(self) -> None:
        result = resolve_project_root(None)
        self.assertTrue(result.is_dir())

    def test_nonexistent_path_raises(self) -> None:
        with self.assertRaises(LabbookError) as ctx:
            resolve_project_root("/nonexistent/path/that/does/not/exist")
        self.assertIn("does not exist", str(ctx.exception))

    def test_file_path_raises(self) -> None:
        with tempfile.NamedTemporaryFile() as tmpfile:
            with self.assertRaises(LabbookError) as ctx:
                resolve_project_root(tmpfile.name)
            self.assertIn("not a directory", str(ctx.exception))


class ClearProjectStateTests(unittest.TestCase):
    def test_clear_session_returns_true_when_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            save_project_session(tmpdir, {"storage": "keychain"})
            self.assertTrue(clear_project_session(tmpdir))

    def test_clear_session_returns_false_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(clear_project_session(tmpdir))

    def test_clear_bindings_returns_false_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(clear_project_bindings(tmpdir))


class LoadJsonEdgeCaseTests(unittest.TestCase):
    def test_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = session_path(tmpdir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not valid json", encoding="utf-8")
            with self.assertRaises(LabbookError) as ctx:
                load_project_session(tmpdir)
            self.assertIn("Invalid JSON", str(ctx.exception))

    def test_non_dict_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = session_path(tmpdir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(LabbookError) as ctx:
                load_project_session(tmpdir)
            self.assertIn("Expected an object", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
