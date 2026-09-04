import asyncio
import sys

import pytest

from codecub.mcp import McpManager, McpServerConfig
from codecub.models import FakeModelClient
from codecub.runtime import Pico
from codecub.sessions import SessionStore
from codecub.tooling.registry import ToolRegistry
from codecub.workspace import WorkspaceContext


MCP_FAKE_SERVER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method", "")
    request_id = request.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "fake-mcp", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{
            "name": "echo",
            "description": "Echo one message",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        }]}
    elif method == "resources/list":
        result = {"resources": [{"uri": "memory://demo", "name": "Demo"}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "demo", "description": "Demo prompt"}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "echo: " + request["params"]["arguments"]["message"]}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"message": "method not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''


def _config():
    return McpServerConfig(
        name="demo",
        command=sys.executable,
        args=("-u", "-c", MCP_FAKE_SERVER),
        timeout_seconds=5,
    )


def test_mcp_stdio_discovery_and_bridge_are_live_registry_capabilities():
    async def scenario():
        registry = ToolRegistry()
        manager = McpManager()
        try:
            snapshot = await manager.connect_servers({"demo": _config()}, registry)
            assert snapshot["errors"] == []
            assert snapshot["servers"][0]["tools"] == ["echo"]
            assert snapshot["servers"][0]["resources"] == ["memory://demo"]
            assert snapshot["servers"][0]["prompts"] == ["demo"]
            tool = registry.resolve("mcp_demo_echo")
            assert tool is not None
            assert tool["effect"] == "external"
            assert tool["parameters"]["required"] == ["message"]
            assert await asyncio.to_thread(tool["run"], {"message": "hello"}) == "echo: hello"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_mcp_tools_use_picos_governed_executor_and_schema_gate(tmp_path):
    async def scenario():
        agent = Pico(
            model_client=FakeModelClient([]),
            workspace=WorkspaceContext.build(tmp_path),
            session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
            approval_policy="auto",
        )
        try:
            snapshot = await agent.connect_mcp_servers({"demo": _config()})
            assert snapshot["servers"][0]["connected"] is True
            assert "mcp_demo_echo" in agent.tools
            recovered, error = agent._recover_native_text_tool_call(
                '<tool>{"name":"mcp_demo_echo","args":{"message":"legacy"}}</tool>'
            )
            assert error is None
            assert recovered.name == "mcp_demo_echo"
            result = await asyncio.to_thread(agent.run_tool, "mcp_demo_echo", {"message": "through executor"})
            assert result == "echo: through executor"
            invalid = await asyncio.to_thread(agent.run_tool, "mcp_demo_echo", {"message": 7})
            assert "invalid arguments" in invalid
            assert agent.tool_executor.last_metadata["tool_error_code"] == "invalid_arguments"
        finally:
            await agent.close_mcp_servers()

    asyncio.run(scenario())


def test_mcp_server_config_rejects_unsafe_http_defaults():
    with pytest.raises(ValueError, match="private|local"):
        McpServerConfig.from_mapping("local", {"transport": "http", "url": "http://127.0.0.1:8080/mcp"})


def test_mcp_server_config_requires_valid_server_name():
    with pytest.raises(ValueError, match="server name"):
        McpServerConfig.from_mapping("bad name", {"command": sys.executable})
