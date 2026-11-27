"""Tests for MCP protocol negotiation without a Snowflake connection."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


class TestMCPProtocol(unittest.TestCase):
    def run_server(self, requests: list[dict[str, Any]]) -> dict[str | int, Any]:
        program = (
            "from unittest.mock import patch\n"
            "from snowflake_mcp_server.main import run_stdio_server\n"
            "from snowflake_mcp_server.utils.snowflake_conn import AuthType, SnowflakeConfig\n"
            "config = SnowflakeConfig(account='testaccount', user='testuser', auth_type=AuthType.EXTERNAL_BROWSER)\n"
            "with patch('snowflake_mcp_server.main.get_snowflake_config', return_value=config), "
            "patch('snowflake.connector.connect', side_effect=RuntimeError('Unexpected login during discovery')):\n"
            "    run_stdio_server()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        return {response["id"]: response for response in responses}

    def legacy_requests(self, version: str = "2024-11-05") -> list[dict[str, Any]]:
        return [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "protocol-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        ]

    def assert_legacy_handshake(self, responses: dict[str | int, Any]) -> None:
        self.assertEqual(responses[2]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(
            {tool["name"] for tool in responses[3]["result"]["tools"]},
            {
                "list_databases",
                "list_views",
                "describe_view",
                "query_view",
                "execute_query",
            },
        )

    def test_discovery_probe_falls_back_to_legacy_handshake(self) -> None:
        for request_id in (1, "discovery"):
            with self.subTest(request_id=request_id):
                probe = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "server/discover",
                    "params": {
                        "_meta": {
                            "io.modelcontextprotocol/client-info": {
                                "name": "protocol-test",
                                "version": "1",
                            }
                        }
                    },
                }
                responses = self.run_server(
                    [probe, *self.legacy_requests("2025-11-25")]
                )
                self.assertEqual(
                    responses[request_id]["error"],
                    {"code": -32601, "message": "Method not found"},
                )
                self.assert_legacy_handshake(responses)

    def test_legacy_client_still_initializes(self) -> None:
        self.assert_legacy_handshake(self.run_server(self.legacy_requests()))


if __name__ == "__main__":
    unittest.main()
