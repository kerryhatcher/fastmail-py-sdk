# Contributing to fastmail-py-sdk

Thanks for your interest in contributing! This guide covers everything you need
to get started.

## Development Setup

**Requirements:**
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)

```bash
git clone https://github.com/kerryhatcher/fastmail-py-sdk.git
cd fastmail-py-sdk
uv sync
```

This installs the SDK and all dev dependencies (pytest, ruff, respx).

## Building and Testing

```bash
# Run the full test suite
uv run pytest

# Run a specific test file
uv run pytest tests/test_caldav_client.py

# Run with verbose output
uv run pytest -v

# Lint and format
uv run ruff check .
uv run ruff format --check .
```

Tests use [respx](https://github.com/lundberg/respx) to mock HTTP responses, so
no Fastmail account is needed to run them.

## Code Style

- Format with **ruff** (`uv run ruff format .`)
- Lint with **ruff** (`uv run ruff check .`)
- Line length: 100 characters
- Use type annotations throughout
- Docstrings follow [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
- All public APIs must have docstrings

The ruff configuration is in `pyproject.toml`. CI will enforce these on every
pull request.

## Pull Request Process

1. **Open an issue** first to discuss what you'd like to change
2. **Fork the repo** and create a feature branch
3. **Write tests** for new functionality
4. **Ensure all tests pass** (`uv run pytest`)
5. **Update docs** if you change public APIs
6. **Submit a PR** with a clear description and link to the issue

PRs should follow [Conventional Commits](https://www.conventionalcommits.org/)
for commit messages. Squash commits before merging.

## Finding Something to Work On

- [Good first issues](https://github.com/kerryhatcher/fastmail-py-sdk/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) are tagged for newcomers
- [Help wanted](https://github.com/kerryhatcher/fastmail-py-sdk/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) are higher-priority tasks
- Check the [roadmap](https://github.com/kerryhatcher/fastmail-py-sdk/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap) for upcoming features

## Architecture

```
src/fastmail_sdk/
├── jmap/          # JMAP client — Fastmail API tokens
├── caldav/        # CalDAV client — app passwords
├── carddav/       # CardDAV client — app passwords
├── models/        # Pydantic models shared across clients
├── config.py      # Credential loading
├── errors.py      # Typed exceptions
└── ical.py        # iCalendar ↔ CalendarEvent parsing/serialization
```

The SDK is a Python port of the Rust [fastmail-cli](https://github.com/kwhatcher/fastmail-cli)
tool. Clients are structured as async context managers using `httpx` for HTTP.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
Please report unacceptable behavior to the project maintainers.