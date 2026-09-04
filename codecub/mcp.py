"""Dependency-light MCP client and ToolRegistry bridge.

The implementation keeps MCP as an extension of the existing tool execution
path.  Remote tools are namespaced, marked external/risky, schema metadata is
retained, and calls still pass through approval, replay, cancellation, and
observation owned by ``ToolExecutor``.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .security import URLSecurityError, validate_url

MCP_MAX_FRAME_BYTES = 1 << 20


class McpError(RuntimeError):
    """MCP transport or protocol failure."""


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    timeout_seconds: float = 30.0
    max_output_chars: int = 100_000
    allow_private_network: bool = False

    @classmethod
    def from_mapping(cls, name: str, raw: Mapping[str, Any]) -> "McpServerConfig":
        transport = str(raw.get("transport") or raw.get("type") or "").strip().lower()
        command = str(raw.get("command") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not transport:
            transport = "stdio" if command else "http"
        if transport in {"streamablehttp", "streamable_http", "http"}:
            transport = "http"
        if transport in {"sse", "http_sse"}:
            transport = "sse"
        if transport not in {"stdio", "http", "sse"}:
            raise ValueError("MCP transport must be stdio, http, or sse")
        if transport == "stdio" and not command:
            raise ValueError("stdio MCP server requires command")
        if transport in {"http", "sse"}:
            if not url:
                raise ValueError(f"{transport} MCP server requires url")
            validate_url(url, allow_private=bool(raw.get("allow_private_network", False)))
        try:
            timeout = float(raw.get("timeout_seconds", raw.get("tool_timeout", 30)))
            max_output = int(raw.get("max_output_chars", 100_000))
        except (TypeError, ValueError) as exc:
            raise ValueError("MCP timeout and output cap must be numeric") from exc
        if timeout <= 0 or max_output <= 0:
            raise ValueError("MCP timeout and output cap must be positive")
        return cls(
            name=_safe_name(name),
            transport=transport,
            command=command,
            args=tuple(str(item) for item in raw.get("args", ()) or ()),
            url=url,
            env={str(key): str(value) for key, value in (raw.get("env", {}) or {}).items()},
            headers={str(key): str(value) for key, value in (raw.get("headers", {}) or {}).items()},
            timeout_seconds=timeout,
            max_output_chars=max_output,
            allow_private_network=bool(raw.get("allow_private_network", False)),
        )


@dataclass(frozen=True, slots=True)
class McpToolDefinition:
    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class McpResourceDefinition:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True, slots=True)
class McpPromptDefinition:
    name: str
    description: str = ""
    arguments: tuple[Mapping[str, Any], ...] = ()


class McpTransport(Protocol):
    async def connect(self) -> dict[str, Any]: ...
    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class _StdioTransport:
    def __init__(self, config: McpServerConfig):
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[Any] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()

    async def connect(self) -> dict[str, Any]:
        if self.process is not None:
            return {}
        environment = dict(os.environ)
        environment.update(self.config.env)
        self.process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "codecub", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})
        return result

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise McpError("MCP stdio transport is not connected")
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
        try:
            async with self._write_lock:
                encoded = (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                if len(encoded) > MCP_MAX_FRAME_BYTES:
                    raise McpError("MCP request exceeds frame limit")
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout=self.config.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise McpError(f"MCP request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            return
        frame = {"jsonrpc": "2.0", "method": method, "params": dict(params)}
        data = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                if len(line) > MCP_MAX_FRAME_BYTES:
                    raise McpError("MCP response exceeds frame limit")
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise McpError("MCP returned invalid JSON") from exc
                request_id = frame.get("id") if isinstance(frame, dict) else None
                future = self._pending.get(request_id)
                if future is None or future.done():
                    continue
                if frame.get("error"):
                    future.set_exception(McpError(str(frame["error"].get("message", "MCP error"))))
                else:
                    result = frame.get("result")
                    future.set_result(result if isinstance(result, dict) else {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(exc if isinstance(exc, McpError) else McpError(str(exc)))

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(McpError("MCP transport closed"))
        self._pending.clear()


class _HttpTransport:
    def __init__(self, config: McpServerConfig):
        self.config = config
        self.session_id = ""

    async def connect(self) -> dict[str, Any]:
        validate_url(
            self.config.url,
            allow_private=self.config.allow_private_network,
            resolve_host=True,
        )
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "codecub", "version": "0.1.0"},
            },
        )
        return result

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": dict(params or {})},
            ensure_ascii=False,
        ).encode("utf-8")

        def post() -> dict[str, Any]:
            request = urllib.request.Request(
                self.config.url,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.config.headers},
                method="POST",
            )
            if self.session_id:
                request.add_header("Mcp-Session-Id", self.session_id)
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    session = response.headers.get("Mcp-Session-Id")
                    if session:
                        self.session_id = session
                    body = response.read(self.config.max_output_chars + 1)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise McpError(f"MCP HTTP request failed: {type(exc).__name__}") from exc
            if len(body) > self.config.max_output_chars:
                raise McpError("MCP HTTP response exceeds output cap")
            text = body.decode("utf-8", errors="replace")
            candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            raw = candidates[-1] if candidates else text
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise McpError("MCP HTTP response is not JSON") from exc
            if frame.get("error"):
                raise McpError(str(frame["error"].get("message", "MCP error")))
            result = frame.get("result", {})
            return result if isinstance(result, dict) else {}

        return await asyncio.wait_for(asyncio.to_thread(post), timeout=self.config.timeout_seconds + 1)

    async def close(self) -> None:
        self.session_id = ""
        return None


class McpClient:
    """One MCP server connection with discovery and one reconnect attempt."""

    def __init__(self, config: McpServerConfig):
        self.config = config
        self.transport: McpTransport = _StdioTransport(config) if config.transport == "stdio" else _HttpTransport(config)
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.tools: tuple[McpToolDefinition, ...] = ()
        self.resources: tuple[McpResourceDefinition, ...] = ()
        self.prompts: tuple[McpPromptDefinition, ...] = ()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        initialized = await self.transport.connect()
        self.server_info = dict(initialized.get("serverInfo") or {})
        self.capabilities = dict(initialized.get("capabilities") or {})
        self._connected = True
        await self.discover()

    async def reconnect(self) -> None:
        async with self._reconnect_lock:
            await self.transport.close()
            self._connected = False
            await self.connect()

    async def discover(self) -> dict[str, Any]:
        tools = await self.transport.request("tools/list")
        self.tools = tuple(
            McpToolDefinition(
                str(item.get("name", "")),
                str(item.get("description", "")),
                dict(item.get("inputSchema") or {"type": "object", "properties": {}}),
            )
            for item in tools.get("tools", ())
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        )
        self.resources = await self._optional_list("resources/list", "resources", McpResourceDefinition)
        self.prompts = await self._optional_list("prompts/list", "prompts", McpPromptDefinition)
        return self.snapshot()

    async def _optional_list(self, method: str, field_name: str, definition_type: type):
        try:
            result = await self.transport.request(method)
        except McpError as exc:
            if "method" in str(exc).lower() and "not" in str(exc).lower():
                return ()
            raise
        values = []
        for item in result.get(field_name, ()):
            if not isinstance(item, dict):
                continue
            if definition_type is McpResourceDefinition:
                values.append(McpResourceDefinition(str(item.get("uri", "")), str(item.get("name", "")), str(item.get("description", "")), str(item.get("mimeType", ""))))
            else:
                values.append(McpPromptDefinition(str(item.get("name", "")), str(item.get("description", "")), tuple(item.get("arguments", ()) or ())))
        return tuple(values)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise McpError("MCP server is not connected")
        try:
            return await self.transport.request("tools/call", {"name": name, "arguments": dict(arguments)})
        except McpError:
            await self.reconnect()
            return await self.transport.request("tools/call", {"name": name, "arguments": dict(arguments)})

    def call_tool_sync(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            return asyncio.run(self.call_tool(name, arguments))
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if loop is running_loop:
            raise McpError("MCP synchronous bridge cannot run on its owner event loop")
        future = asyncio.run_coroutine_threadsafe(self.call_tool(name, arguments), loop)
        try:
            return future.result(timeout=self.config.timeout_seconds + 2)
        except TimeoutError as exc:
            future.cancel()
            raise McpError("MCP tool call timed out") from exc

    def snapshot(self) -> dict[str, Any]:
        return {
            "server": self.config.name,
            "transport": self.config.transport,
            "connected": self._connected,
            "server_info": dict(self.server_info),
            "capabilities": dict(self.capabilities),
            "tools": [item.name for item in self.tools],
            "resources": [item.uri for item in self.resources],
            "prompts": [item.name for item in self.prompts],
        }

    async def close(self) -> None:
        self._connected = False
        await self.transport.close()


class McpToolBridge:
    """Map one MCP tool to the existing synchronous ToolRegistry spec."""

    def __init__(self, client: McpClient, definition: McpToolDefinition):
        self.client = client
        self.definition = definition
        self.name = f"mcp_{client.config.name}_{definition.name}"

    def run(self, args: Mapping[str, Any]) -> str:
        result = self.client.call_tool_sync(self.definition.name, args)
        parts = []
        for item in result.get("content", ()):
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        text = "\n".join(parts) or "(no output)"
        if result.get("isError"):
            return f"error: MCP tool {self.name} failed: {text}"
        return text[: self.client.config.max_output_chars]

    def spec(self) -> dict[str, Any]:
        return {
            "schema": {},
            "parameters": dict(self.definition.input_schema),
            "risky": True,
            "side_effect": True,
            "idempotent": False,
            "retryable": False,
            "effect": "external",
            "concurrency_safe": False,
            "timeout_seconds": self.client.config.timeout_seconds,
            "description": self.definition.description or self.definition.name,
            "run": self.run,
            "mcp_server": self.client.config.name,
            "mcp_original_name": self.definition.name,
        }


class McpManager:
    """Own MCP connection lifecycle and register discovered tools explicitly."""

    def __init__(self):
        self.clients: dict[str, McpClient] = {}
        self.errors: list[dict[str, str]] = []

    async def connect_servers(self, configs: Mapping[str, Mapping[str, Any] | McpServerConfig], registry) -> dict[str, Any]:
        for name, raw in configs.items():
            client = None
            registered_names: list[str] = []
            try:
                config = raw if isinstance(raw, McpServerConfig) else McpServerConfig.from_mapping(name, raw)
                client = McpClient(config)
                await client.connect()
                for definition in client.tools:
                    bridge = McpToolBridge(client, definition)
                    registry.register(bridge.name, bridge.spec())
                    registered_names.append(bridge.name)
                self.clients[config.name] = client
            except (ValueError, URLSecurityError, McpError) as exc:
                if client is not None:
                    await client.close()
                unregister = getattr(registry, "unregister", None)
                if callable(unregister):
                    for registered_name in registered_names:
                        unregister(registered_name)
                self.errors.append({"server": str(name), "error": str(exc)})
        return self.snapshot()

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self.clients.values()), return_exceptions=True)
        self.clients.clear()

    def snapshot(self) -> dict[str, Any]:
        return {"servers": [client.snapshot() for client in self.clients.values()], "errors": list(self.errors)}


def _safe_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
        raise ValueError("MCP server name must contain only letters, digits, '_' or '-'")
    return name


__all__ = [
    "MCP_MAX_FRAME_BYTES",
    "McpClient",
    "McpError",
    "McpManager",
    "McpPromptDefinition",
    "McpResourceDefinition",
    "McpServerConfig",
    "McpToolBridge",
    "McpToolDefinition",
]
