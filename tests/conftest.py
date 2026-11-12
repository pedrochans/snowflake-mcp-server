"""Shared test fixtures."""

import pytest

from snowflake_mcp_server.utils import snowflake_conn


@pytest.fixture(autouse=True)
def _no_wsl_browser_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep connection tests from altering PATH when run under WSL."""
    monkeypatch.setattr(snowflake_conn, "install_wsl_browser_shim", lambda: None)
