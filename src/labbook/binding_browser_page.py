from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("labbook.binding_browser_page")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "binding_chooser.html"
_PLACEHOLDER = "/*__LABBOOK_CONFIG_JSON__*/null"
_cached_template: str | None = None


def _load_template() -> str:
    """Load the HTML template, caching it after the first read."""
    global _cached_template
    if _cached_template is None:
        _cached_template = _TEMPLATE_PATH.read_text(encoding="utf-8")
        logger.debug(
            "loaded binding chooser template (%d bytes)", len(_cached_template)
        )
    return _cached_template


def _inline_json(value: Any) -> str:
    """Serialize *value* to JSON with HTML-safe escaping.

    The three replacements prevent the JSON literal from breaking out of
    a ``<script>`` context or being interpreted as an HTML entity.
    """
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_binding_browser_page(payload: dict[str, Any]) -> str:
    """Return a complete HTML page for the binding chooser UI.

    *payload* is serialized as JSON and injected into the template as
    the ``config`` constant consumed by the client-side JavaScript.
    """
    template = _load_template()
    config_json = _inline_json(payload)
    return template.replace(_PLACEHOLDER, config_json, 1)
