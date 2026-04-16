"""Tests for binding chooser template shell and chooser app script."""

from __future__ import annotations

import unittest

from labbook.binding_server import (
    _inline_json,
    _load_chooser_app_script,
    render_chooser_page,
)


class InlineJsonTests(unittest.TestCase):
    def test_basic_serialization(self) -> None:
        result = _inline_json({"key": "value"})
        self.assertIn('"key"', result)
        self.assertIn('"value"', result)

    def test_escapes_ampersand(self) -> None:
        result = _inline_json({"text": "a & b"})
        self.assertNotIn("&", result)
        self.assertIn("\\u0026", result)

    def test_escapes_less_than(self) -> None:
        result = _inline_json({"text": "<script>"})
        self.assertNotIn("<", result)
        self.assertIn("\\u003c", result)

    def test_escapes_greater_than(self) -> None:
        result = _inline_json({"text": "a > b"})
        self.assertNotIn(">", result)
        self.assertIn("\\u003e", result)

    def test_combined_xss_payload(self) -> None:
        result = _inline_json({"xss": "</script><img src=x onerror=alert(1)>"})
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn("&", result)

    def test_unicode_preserved(self) -> None:
        result = _inline_json({"name": "工作区"})
        self.assertIn("工作区", result)

    def test_empty_dict(self) -> None:
        result = _inline_json({})
        self.assertEqual(result, "{}")


class RenderChooserPageTests(unittest.TestCase):
    def test_returns_html_with_config(self) -> None:
        html = render_chooser_page({"project_root": "/tmp/test", "page_size": 25})
        self.assertIn("<!doctype html>", html)
        self.assertIn("Agent Labbook Binding Chooser", html)
        self.assertIn("/tmp/test", html)
        self.assertIn("25", html)
        self.assertIn('id="labbook-config"', html)
        self.assertIn('src="./assets/binding_chooser_app.js"', html)

    def test_placeholder_is_replaced(self) -> None:
        html = render_chooser_page({"key": "value"})
        self.assertNotIn("__LABBOOK_CONFIG_JSON__", html)
        self.assertIn('"key"', html)
        self.assertIn('"value"', html)

    def test_xss_safe_in_rendered_html(self) -> None:
        html = render_chooser_page({"project_root": '<script>alert("xss")</script>'})
        self.assertNotIn('<script>alert("xss")</script>', html)
        self.assertIn("\\u003cscript\\u003e", html)

    def test_html_shell_keeps_config_non_executable(self) -> None:
        html = render_chooser_page({"project_root": "/tmp/test"})
        self.assertIn('<script id="labbook-config" type="application/json">', html)
        self.assertNotIn("window.__LABBOOK_CONFIG__", html)
        self.assertNotIn("<script type=\"module\">", html)

    def test_template_caching(self) -> None:
        html1 = render_chooser_page({"a": 1})
        html2 = render_chooser_page({"b": 2})
        self.assertIn("<!doctype html>", html1)
        self.assertIn("<!doctype html>", html2)
        self.assertIn('"a"', html1)
        self.assertIn('"b"', html2)


class ChooserAppScriptTests(unittest.TestCase):
    def test_checkbox_checked_state_does_not_depend_on_tailwind_palette(self) -> None:
        script = _load_chooser_app_script()
        self.assertNotIn("colors: { stone: undefined }", script)
        self.assertIn('backgroundColor: checked ? "#1c1917" : "#ffffff"', script)
        self.assertIn('borderColor: checked ? "#1c1917" : "#d6d3d1"', script)

    def test_tree_projection_uses_single_helper(self) -> None:
        script = _load_chooser_app_script()
        self.assertIn("function projectTreeState({", script)
        self.assertIn("const treeState = projectTreeState({", script)
        self.assertNotIn("function reduceToSearchRoots(matchIds, graph)", script)
        self.assertNotIn("function buildVisibleRows({", script)

    def test_tree_projection_preserves_search_lineage_and_subtree(self) -> None:
        script = _load_chooser_app_script()
        self.assertIn("addLineage(rid);", script)
        self.assertIn(
            "walkSubtree(rid, (childId) => visibleIds.add(childId));", script
        )
        self.assertIn("if (matchIds.has(current)) {", script)

    def test_manual_tree_collapse_still_controls_expansion(self) -> None:
        script = _load_chooser_app_script()
        self.assertIn(
            "if (!hasKnownChildren && !hasBeenFetched) return false;", script
        )
        self.assertIn("return !collapsedIds.has(rid);", script)

    def test_success_state_renders_completion_view(self) -> None:
        script = _load_chooser_app_script()
        self.assertIn("Binding Complete", script)
        self.assertIn("You can close this tab.", script)

    def test_module_reads_json_config_instead_of_global_assignment(self) -> None:
        script = _load_chooser_app_script()
        self.assertIn(
            'document.getElementById("labbook-config")?.textContent', script
        )
        self.assertNotIn("window.__LABBOOK_CONFIG__", script)
        self.assertNotIn("useRef", script)
        self.assertNotIn("Fragment", script)


if __name__ == "__main__":
    unittest.main()
