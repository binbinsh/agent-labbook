# Contributing

Thank you for considering a contribution to Notion Agent Labbook.

## Development Setup

```bash
git clone https://github.com/binbinsh/agent-labbook.git
cd agent-labbook
uv sync
```

## Running Tests

```bash
uv run python -m pytest tests/ -v
```

All tests must pass before submitting a pull request.

## Project Structure

```
src/labbook/          # Package source
  mcp_server.py       # MCP tool/resource/prompt definitions
  auth_flow.py        # Secret detection and storage orchestration
  notion_api.py       # Notion HTTP client with retry
  binding_ops.py      # Binding CRUD and search
  binding_discovery.py # Child discovery and normalization
  binding_browser_page.py # HTML template renderer
  binding_ui.py       # Local HTTP server for browser binding UI
  state.py            # .labbook/ persistence
  cli.py              # CLI entry points
  templates/          # HTML templates
tests/                # Test suite
docs/                 # Additional documentation
```

## Code Style

- Python 3.10+ syntax. No backports for older versions.
- Type hints on all public function signatures.
- Use `logging.getLogger("labbook.<module>")` for structured logging.
- Avoid bare `except:`. Use `except Exception:` only where justified.
- No unused imports.

## Testing Guidelines

- New features and bug fixes should include tests.
- Tests use `unittest.TestCase` and `unittest.mock`. No external test fixtures.
- Keep tests fast: mock all I/O (HTTP, filesystem, keyring, subprocess).
- Test file naming: `tests/test_<module>.py`.

## Pull Requests

1. Fork the repository and create a feature branch.
2. Make your changes with tests.
3. Run `uv run python -m pytest tests/ -v` and confirm all tests pass.
4. Open a pull request against `main`.

## Security

If you discover a security vulnerability, please report it privately. See [SECURITY.md](./SECURITY.md) for details.
