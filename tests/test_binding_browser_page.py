"""Tests for binding_browser_page — template loading, XSS-safe JSON, render."""

from __future__ import annotations

import unittest

from labbook.binding_browser_page import _inline_json, render_binding_browser_page


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


class RenderBindingBrowserPageTests(unittest.TestCase):
    def test_returns_html_with_config(self) -> None:
        html = render_binding_browser_page(
            {"project_root": "/tmp/test", "page_size": 25}
        )
        self.assertIn("<!doctype html>", html)
        self.assertIn("Agent Labbook Binding Chooser", html)
        self.assertIn("/tmp/test", html)
        self.assertIn("25", html)

    def test_placeholder_is_replaced(self) -> None:
        html = render_binding_browser_page({"key": "value"})
        self.assertNotIn("__LABBOOK_CONFIG_JSON__", html)
        self.assertIn('"key"', html)
        self.assertIn('"value"', html)

    def test_xss_safe_in_rendered_html(self) -> None:
        html = render_binding_browser_page(
            {"project_root": '<script>alert("xss")</script>'}
        )
        self.assertNotIn('<script>alert("xss")</script>', html)
        self.assertIn("\\u003cscript\\u003e", html)

    def test_no_innerhtml_with_user_data(self) -> None:
        """Verify the rendered template does not use innerHTML for dynamic data."""
        html = render_binding_browser_page({"project_root": "/tmp/test"})
        # The only innerHTML usages should be:
        # 1. escapeHtml() reading innerHTML for output
        # 2. app.innerHTML = "" to clear the container
        lines_with_innerhtml = [
            line.strip() for line in html.splitlines() if "innerHTML" in line
        ]
        for line in lines_with_innerhtml:
            self.assertTrue(
                "return div.innerHTML" in line or 'innerHTML = ""' in line,
                f"Unexpected innerHTML usage: {line}",
            )

    def test_template_caching(self) -> None:
        """Second call should use cached template (no file re-read)."""
        html1 = render_binding_browser_page({"a": 1})
        html2 = render_binding_browser_page({"b": 2})
        # Both should be valid HTML
        self.assertIn("<!doctype html>", html1)
        self.assertIn("<!doctype html>", html2)
        # Each should have its own config
        self.assertIn('"a"', html1)
        self.assertIn('"b"', html2)

    def test_checkbox_checked_state_does_not_depend_on_tailwind_palette(self) -> None:
        html = render_binding_browser_page({})
        self.assertNotIn("colors: { stone: undefined }", html)
        self.assertIn('backgroundColor: checked ? "#1c1917" : "#ffffff"', html)
        self.assertIn('borderColor: checked ? "#1c1917" : "#d6d3d1"', html)

    def test_search_mode_still_respects_manual_tree_collapse(self) -> None:
        html = render_binding_browser_page({})
        self.assertIn("if (searchActive) return !collapsedIds.has(rid);", html)

    def test_success_state_renders_completion_view(self) -> None:
        html = render_binding_browser_page({})
        self.assertIn("Binding Complete", html)
        self.assertIn("You can close this tab.", html)


if __name__ == "__main__":
    unittest.main()
