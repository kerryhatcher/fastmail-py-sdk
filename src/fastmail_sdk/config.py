"""Credential loading — env vars first, then config file.

Reads ``FASTMAIL_USERNAME`` and ``FASTMAIL_APP_PASSWORD`` from the
environment, falling back to ``~/.config/fastmail-cli/config.toml``
for compatibility with the Rust ``fastmail-cli`` tool.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastmail_sdk.errors import NotAuthenticated


def _config_path() -> Path:
    return Path.home() / ".config" / "fastmail-cli" / "config.toml"


def load_credentials() -> tuple[str, str]:
    """Return ``(username, app_password)`` for CalDAV authentication.

    Precedence:
    1. ``FASTMAIL_USERNAME`` / ``FASTMAIL_APP_PASSWORD`` env vars
    2. ``[contacts]`` section of ``~/.config/fastmail-cli/config.toml``

    Raises:
        NotAuthenticated: If credentials cannot be found.
    """
    username = os.environ.get("FASTMAIL_USERNAME")
    app_password = os.environ.get("FASTMAIL_APP_PASSWORD")

    if username and app_password:
        return username, app_password

    config_file = _config_path()
    if config_file.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        data = tomllib.loads(config_file.read_text())
        contacts = data.get("contacts", {})
        if not username:
            username = contacts.get("username")
        if not app_password:
            app_password = contacts.get("app_password")

    if not username or not app_password:
        raise NotAuthenticated(
            "Fastmail CalDAV credentials not found. "
            "Set FASTMAIL_USERNAME and FASTMAIL_APP_PASSWORD environment variables, "
            "or configure [contacts] in ~/.config/fastmail-cli/config.toml."
        )

    return username, app_password
