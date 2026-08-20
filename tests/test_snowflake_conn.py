"""Tests for Snowflake connection utilities."""

import pip_system_certs.wrapt_requests  # noqa: F401  # isort: skip

from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from snowflake_mcp_server.utils.snowflake_conn import (
    AuthType,
    SnowflakeConfig,
    SnowflakeConnectionManager,
    get_snowflake_connection,
)


@pytest.fixture
def mock_private_key() -> rsa.RSAPrivateKey:
    """Mock a private key."""
    return MagicMock(spec=rsa.RSAPrivateKey)


@pytest.fixture
def snowflake_config_private_key() -> SnowflakeConfig:
    """Create a sample Snowflake configuration with private key auth."""
    return SnowflakeConfig(
        account="testaccount",
        user="testuser",
        auth_type=AuthType.PRIVATE_KEY,
        private_key_path="/path/to/key.p8",
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
        role="test_role",
    )


@pytest.fixture
def snowflake_config_browser() -> SnowflakeConfig:
    """Create a sample Snowflake configuration with external browser auth."""
    return SnowflakeConfig(
        account="testaccount",
        user="testuser",
        auth_type=AuthType.EXTERNAL_BROWSER,
        warehouse="test_warehouse",
        database="test_database",
        schema_name="test_schema",
        role="test_role",
    )


@patch("snowflake_mcp_server.utils.snowflake_conn.load_private_key")
@patch("snowflake.connector.connect")
def test_get_snowflake_connection_private_key(
    mock_connect: MagicMock,
    mock_load_key: MagicMock,
    snowflake_config_private_key: SnowflakeConfig,
    mock_private_key: rsa.RSAPrivateKey,
) -> None:
    """Test creating a Snowflake connection with private key auth."""
    # Setup mocks
    mock_load_key.return_value = mock_private_key
    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    # Call function
    conn = get_snowflake_connection(snowflake_config_private_key)

    # Assertions
    mock_load_key.assert_called_once_with(snowflake_config_private_key.private_key_path)
    mock_connect.assert_called_once_with(
        account=snowflake_config_private_key.account,
        user=snowflake_config_private_key.user,
        private_key=mock_private_key,
        warehouse=snowflake_config_private_key.warehouse,
        database=snowflake_config_private_key.database,
        schema=snowflake_config_private_key.schema_name,
        role=snowflake_config_private_key.role,
    )
    assert conn == mock_connection


@patch("snowflake.connector.connect")
def test_get_snowflake_connection_browser_auth(
    mock_connect: MagicMock,
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    """Test creating a Snowflake connection with external browser auth."""
    # Setup mocks
    mock_connection = MagicMock()
    mock_connect.return_value = mock_connection

    # Call function
    conn = get_snowflake_connection(snowflake_config_browser)

    # Assertions
    mock_connect.assert_called_once_with(
        account=snowflake_config_browser.account,
        user=snowflake_config_browser.user,
        authenticator="externalbrowser",
        client_store_temporary_credential=True,
        warehouse=snowflake_config_browser.warehouse,
        database=snowflake_config_browser.database,
        schema=snowflake_config_browser.schema_name,
        role=snowflake_config_browser.role,
    )
    assert conn == mock_connection


def test_run_with_connection_runs_operation_under_lock() -> None:
    """run_with_connection passes the live connection to the operation and
    returns its result without reconnecting when the connection is healthy."""
    mgr = SnowflakeConnectionManager()  # singleton
    sentinel_conn = MagicMock()
    prev_conn, prev_healthy = mgr._connection, mgr._connection_healthy
    try:
        mgr._connection = sentinel_conn
        sentinel_conn.is_closed.return_value = False
        mgr._connection_healthy = True
        received = {}

        def op(conn: object) -> str:
            received["conn"] = conn
            return "result"

        assert mgr.run_with_connection(op) == "result"
        assert received["conn"] is sentinel_conn
        # The lock must be released again after the operation completes.
        assert mgr._connection_lock.acquire(blocking=False)
        mgr._connection_lock.release()
    finally:
        mgr._connection, mgr._connection_healthy = prev_conn, prev_healthy


def test_run_with_connection_requires_initialized_config() -> None:
    """run_with_connection raises if there is no connection and no config."""
    mgr = SnowflakeConnectionManager()  # singleton
    prev_conn, prev_healthy, prev_config = (
        mgr._connection,
        mgr._connection_healthy,
        mgr._config,
    )
    try:
        mgr._connection = None
        mgr._connection_healthy = False
        mgr._config = None
        mgr._last_failed_connect = None  # not in connect cooldown
        with pytest.raises(ValueError):
            mgr.run_with_connection(lambda conn: conn)
    finally:
        mgr._connection = prev_conn
        mgr._connection_healthy = prev_healthy
        mgr._config = prev_config


def test_run_with_connection_honors_failure_cooldown() -> None:
    """Within the cooldown after a failed connect, re-raise the cached error
    instead of attempting another (interactive) connection."""
    from datetime import datetime

    mgr = SnowflakeConnectionManager()  # singleton
    saved = (
        mgr._connection,
        mgr._connection_healthy,
        mgr._last_failed_connect,
        mgr._last_error,
    )
    try:
        mgr._connection = None
        mgr._connection_healthy = False
        mgr._last_error = RuntimeError("auth failed")
        mgr._last_failed_connect = datetime.now()  # just failed -> in cooldown
        with patch.object(mgr, "_connect") as mock_connect:
            with pytest.raises(RuntimeError, match="auth failed"):
                mgr.run_with_connection(lambda conn: conn)
            mock_connect.assert_not_called()
    finally:
        (
            mgr._connection,
            mgr._connection_healthy,
            mgr._last_failed_connect,
            mgr._last_error,
        ) = saved


def test_run_with_connection_reconnects_on_expired_session(
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    """A stale session that fails mid-operation triggers one reconnect+retry."""
    from snowflake.connector.errors import DatabaseError

    mgr = SnowflakeConnectionManager()  # singleton
    saved = (
        mgr._connection,
        mgr._connection_healthy,
        mgr._last_failed_connect,
        mgr._last_error,
        mgr._config,
    )
    try:
        old_conn = MagicMock()
        old_conn.is_closed.return_value = False
        new_conn = MagicMock()
        new_conn.is_closed.return_value = False
        mgr._connection = old_conn
        mgr._connection_healthy = True
        mgr._last_failed_connect = None
        mgr._config = snowflake_config_browser

        def fake_connect() -> None:
            mgr._connection = new_conn
            mgr._connection_healthy = True
            mgr._last_failed_connect = None

        seen = []

        def op(conn: object) -> str:
            seen.append(conn)
            if conn is old_conn:
                raise DatabaseError(
                    msg="Authentication token has expired", errno=390114
                )
            return "ok"

        with patch.object(mgr, "_connect", side_effect=fake_connect):
            assert mgr.run_with_connection(op) == "ok"
        assert seen == [old_conn, new_conn]  # retried on the fresh connection
    finally:
        (
            mgr._connection,
            mgr._connection_healthy,
            mgr._last_failed_connect,
            mgr._last_error,
            mgr._config,
        ) = saved


@patch("snowflake.connector.connect")
def test_statement_timeout_passed_as_session_parameter(
    mock_connect: MagicMock,
) -> None:
    """statement_timeout_seconds becomes STATEMENT_TIMEOUT_IN_SECONDS."""
    config = SnowflakeConfig(
        account="test_account",
        user="test_user",
        auth_type=AuthType.EXTERNAL_BROWSER,
        statement_timeout_seconds=120,
    )
    get_snowflake_connection(config)
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["session_parameters"] == {"STATEMENT_TIMEOUT_IN_SECONDS": 120}


@patch("snowflake.connector.connect")
def test_statement_timeout_omitted_when_unset(mock_connect: MagicMock) -> None:
    config = SnowflakeConfig(
        account="test_account",
        user="test_user",
        auth_type=AuthType.EXTERNAL_BROWSER,
    )
    get_snowflake_connection(config)
    assert "session_parameters" not in mock_connect.call_args.kwargs
