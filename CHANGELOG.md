# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.17.2 -- 2026-04-13

### Added
- CI workflow with Python 3.10--3.14 matrix testing.
- PEP 561 `py.typed` marker for downstream type checkers.
- Retry logic with exponential backoff for Notion API calls (429, 502, 503, 504, network errors).
- CSRF origin validation on the local binding browser HTTP handler.
- Structured `logging.getLogger("labbook.<module>")` across all modules.
- Safety upper bounds on recursive/iterative operations (`_MAX_ALIAS_SUFFIX`, `_MAX_BLOCK_CHILDREN_PER_CONTAINER`).
- `CONTRIBUTING.md` with development setup and testing instructions.
- Architecture overview in README.

### Changed
- `binding_browser_page.py` rewritten: 1129-line f-string replaced with an external HTML template and a 49-line Python loader. All `innerHTML` replaced with safe DOM APIs.
- Synchronous MCP tool handlers wrapped in `asyncio.to_thread()` to avoid blocking the event loop.
- Version number sourced from `importlib.metadata` instead of a hardcoded constant.
- `list.pop(0)` in `binding_discovery.py` replaced with `collections.deque.popleft()`.
- `publish-pypi.yml` now runs all tests via pytest instead of a hardcoded subset.

### Removed
- Dead `keyring is None` guard branches (keyring is a hard dependency).
- Unused imports and duplicate `import json`.
- Backward-compatibility wrappers and trivial single-use helper functions.

### Fixed
- XSS vulnerability in the binding browser page (unsafe `innerHTML` with user-controlled data).
- Secret access now logged at INFO level for auditability.

## 0.17.1 -- 2026-04-06

### Changed
- Restored package name to `agent-labbook` on PyPI (was briefly renamed to `notion-agent-labbook` in 0.17.0).

## 0.17.0 -- 2026-04-06

### Changed
- **Breaking:** Replaced OAuth/Cloudflare Worker model with direct Notion Internal Integration secret management.
- **Breaking:** Session files from versions prior to 0.17.0 are rejected on load. Delete `.labbook/` and reconfigure.
- Removed all Cloudflare Worker and Node.js toolchain dependencies.

## 0.14.7 -- 2026-04-03

### Added
- Markdown-first Notion usage documentation.

## 0.14.6

### Changed
- Tightened connect prompting and URL relay hints.

## 0.14.5

### Changed
- Guide connect decisions before Notion auth.

## 0.14.0 -- 0.14.4

### Changed
- Various improvements to MCP client guidance, SSH session detection, browser flow handling, and headless auth defaults.

## 0.13.x

Legacy OAuth-based releases. Superseded by 0.17.0.
