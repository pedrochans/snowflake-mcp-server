"""Tests for Snowflake connection utilities."""

# Import pip-system-certs first to ensure SSL uses Windows certificate store
# This is crucial for corporate environments with custom SSL certificates
import pip_system_certs.wrapt_requests  # noqa: F401

from datetime import datetime, timedelta
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


@pytest.fixture
def connection_manager(monkeypatch: pytest.MonkeyPatch) -> SnowflakeConnectionManager:
    monkeypatch.setattr(SnowflakeConnectionManager, "_instance", None)
    monkeypatch.setenv("SNOWFLAKE_CONN_REFRESH_HOURS", "8")
    return SnowflakeConnectionManager()


@patch("snowflake_mcp_server.utils.snowflake_conn.get_snowflake_connection")
def test_browser_auth_connects_only_on_demand(
    mock_connect: MagicMock,
    connection_manager: SnowflakeConnectionManager,
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    mock_connect.return_value.is_closed.return_value = False

    connection_manager.initialize(snowflake_config_browser)

    mock_connect.assert_not_called()
    assert connection_manager._refresh_thread is None
    assert connection_manager.get_connection() is mock_connect.return_value
    assert connection_manager.get_connection() is mock_connect.return_value
    mock_connect.assert_called_once_with(snowflake_config_browser)


@patch("snowflake_mcp_server.utils.snowflake_conn.get_snowflake_connection")
def test_browser_auth_refreshes_only_on_next_request(
    mock_connect: MagicMock,
    connection_manager: SnowflakeConnectionManager,
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    mock_connect.return_value.is_closed.return_value = False
    connection_manager.initialize(snowflake_config_browser)
    connection_manager.get_connection()
    mock_connect.reset_mock()
    connection_manager._last_refresh_time = datetime.now() - timedelta(hours=9)

    with patch.object(connection_manager._stop_event, "wait", side_effect=[False, True]):
        connection_manager._refresh_connection_periodically()

    mock_connect.assert_not_called()
    connection_manager.get_connection()
    mock_connect.assert_called_once_with(snowflake_config_browser)


@patch("snowflake_mcp_server.utils.snowflake_conn.get_snowflake_connection")
def test_browser_auth_reconnects_closed_connection_on_demand(
    mock_connect: MagicMock,
    connection_manager: SnowflakeConnectionManager,
    snowflake_config_browser: SnowflakeConfig,
) -> None:
    mock_connect.return_value.is_closed.return_value = False
    connection_manager.initialize(snowflake_config_browser)
    connection_manager.get_connection()
    mock_connect.reset_mock()
    mock_connect.return_value.is_closed.return_value = True

    connection_manager.get_connection()

    mock_connect.assert_called_once_with(snowflake_config_browser)


@patch("snowflake_mcp_server.utils.snowflake_conn.threading.Thread")
@patch("snowflake_mcp_server.utils.snowflake_conn.get_snowflake_connection")
def test_private_key_auth_keeps_background_refresh(
    mock_connect: MagicMock,
    mock_thread: MagicMock,
    connection_manager: SnowflakeConnectionManager,
    snowflake_config_private_key: SnowflakeConfig,
) -> None:
    connection_manager.initialize(snowflake_config_private_key)
    mock_connect.assert_called_once_with(snowflake_config_private_key)
    mock_thread.return_value.start.assert_called_once()
    mock_connect.reset_mock()
    connection_manager._last_refresh_time = datetime.now() - timedelta(hours=9)

    with patch.object(connection_manager._stop_event, "wait", side_effect=[False, True]):
        connection_manager._refresh_connection_periodically()

    mock_connect.assert_called_once_with(snowflake_config_private_key)


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
        client_session_keep_alive=False,
        warehouse=snowflake_config_browser.warehouse,
        database=snowflake_config_browser.database,
        schema=snowflake_config_browser.schema_name,
        role=snowflake_config_browser.role,
    )
    assert conn == mock_connection
