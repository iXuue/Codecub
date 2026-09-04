"""Bounded newline-delimited JSON-RPC Gateway transport.

The server owns only transport, authentication, request routing, streaming
notifications, and connection lifecycle.  Runtime behaviour is delegated to
``EmbeddedRuntimeGateway`` through a narrow port.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from .auth import Identity, StaticTokenAuthProvider
from .gateway_runtime import EmbeddedRuntimeGateway, RuntimeGatewayError

MAX_RPC_FRAME_BYTES = 1 << 20
DEFAULT_OUTBOUND_QUEUE_SIZE = 128


class GatewayRpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data


class _Connection:
    def __init__(self, server: "GatewayServer", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.loop = asyncio.get_running_loop()
        self.authenticated = server.auth_provider is None
        self.identity: Identity | None = (
            Identity("local", frozenset({"run:*", "tool:*"}), auth_method="local")
            if self.authenticated
            else None
        )
        self.outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=server.outbound_queue_size)
        self.pending: set[asyncio.Task[Any]] = set()
        self.unsubscribers: list[Callable[[], None]] = []
        self.closed = False

    async def run(self) -> None:
        writer_task = asyncio.create_task(self._write_loop())
        try:
            while not self.closed:
                try:
                    line = await self.reader.readline()
                except asyncio.LimitOverrunError:
                    await self.send_error(None, -32600, "frame_too_large")
                    break
                if not line:
                    break
                if len(line) > self.server.max_frame_bytes:
                    await self.send_error(None, -32600, "frame_too_large")
                    break
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    await self.send_error(None, -32700, "parse_error", {"reason": str(exc)})
                    continue
                task = asyncio.create_task(self.server.dispatch(self, frame))
                self.pending.add(task)
                task.add_done_callback(self.pending.discard)
        finally:
            self.closed = True
            for task in tuple(self.pending):
                if not task.done():
                    task.cancel()
            if self.pending:
                await asyncio.gather(*self.pending, return_exceptions=True)
            for unsubscribe in self.unsubscribers:
                unsubscribe()
            writer_task.cancel()
            await asyncio.gather(writer_task, return_exceptions=True)
            self.writer.close()
            await self.writer.wait_closed()

    async def send(self, frame: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.outbound.put_nowait(frame)
        except asyncio.QueueFull as exc:
            self.closed = True
            self.writer.close()
            raise GatewayRpcError(-32001, "connection outbound queue is full") from exc

    async def send_error(self, request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        await self.send({"jsonrpc": "2.0", "id": request_id, "error": error})

    def notify_from_thread(self, event: dict[str, Any], run_id: str = "") -> None:
        def enqueue() -> None:
            if self.closed:
                return
            if run_id and event.get("run_id") != run_id:
                return
            try:
                self.outbound.put_nowait(
                    {"jsonrpc": "2.0", "method": "run.event", "params": event}
                )
            except asyncio.QueueFull:
                self.closed = True
                self.writer.close()

        self.loop.call_soon_threadsafe(enqueue)

    async def _write_loop(self) -> None:
        while not self.closed:
            frame = await self.outbound.get()
            try:
                data = (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                if len(data) > self.server.max_frame_bytes:
                    self.closed = True
                    return
                self.writer.write(data)
                await self.writer.drain()
            finally:
                self.outbound.task_done()


class GatewayServer:
    """Serve the Runtime RPC contract over one bounded TCP listener."""

    def __init__(
        self,
        runtime: EmbeddedRuntimeGateway,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        auth_token: str | None = None,
        auth_provider: Any = None,
        allow_unauthenticated: bool = False,
        max_frame_bytes: int = MAX_RPC_FRAME_BYTES,
        outbound_queue_size: int = DEFAULT_OUTBOUND_QUEUE_SIZE,
    ) -> None:
        if auth_token is None and auth_provider is None and not allow_unauthenticated:
            raise ValueError("auth_token is required unless allow_unauthenticated=True")
        if max_frame_bytes <= 0 or outbound_queue_size <= 0:
            raise ValueError("Gateway limits must be greater than zero")
        self.runtime = runtime
        self.host = str(host)
        self.port = int(port)
        self.auth_token = str(auth_token) if auth_token is not None else None
        self.auth_provider = auth_provider or (
            StaticTokenAuthProvider(self.auth_token) if self.auth_token is not None else None
        )
        self.max_frame_bytes = int(max_frame_bytes)
        self.outbound_queue_size = int(outbound_queue_size)
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[_Connection] = set()

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("GatewayServer has not started")
        address = self._server.sockets[0].getsockname()
        return str(address[0]), int(address[1])

    async def start(self) -> tuple[str, int]:
        if self._server is not None:
            return self.address
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            limit=self.max_frame_bytes + 1,
        )
        return self.address

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        connections = tuple(self._connections)
        for connection in connections:
            connection.closed = True
            connection.writer.close()
        if connections:
            await asyncio.gather(*(connection.writer.wait_closed() for connection in connections), return_exceptions=True)
        await self.runtime.stop_automation()
        await asyncio.to_thread(self.runtime.close)

    async def dispatch(self, connection: _Connection, frame: Any) -> None:
        request_id = frame.get("id") if isinstance(frame, dict) else None
        try:
            method, params = self._validate_frame(frame)
            if not connection.authenticated and method != "gateway.auth":
                raise GatewayRpcError(-32010, "authentication_required")
            result = await self._invoke(connection, method, params)
            if isinstance(frame, dict) and "id" not in frame:
                return
            await connection.send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except GatewayRpcError as exc:
            if isinstance(frame, dict) and "id" not in frame:
                return
            try:
                await connection.send_error(request_id, exc.code, exc.message, exc.data)
            except GatewayRpcError:
                return
        except RuntimeGatewayError as exc:
            if isinstance(frame, dict) and "id" not in frame:
                return
            await connection.send_error(request_id, -32020, "runtime_error", {"detail": str(exc)})
        except Exception:
            if isinstance(frame, dict) and "id" not in frame:
                return
            await connection.send_error(request_id, -32603, "internal_error")

    @staticmethod
    def _validate_frame(frame: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
            raise GatewayRpcError(-32600, "invalid_request")
        method = frame.get("method")
        if not isinstance(method, str) or not method.strip():
            raise GatewayRpcError(-32600, "invalid_request")
        params = frame.get("params") or {}
        if not isinstance(params, dict):
            raise GatewayRpcError(-32602, "invalid_params")
        return method.strip(), params

    async def _invoke(self, connection: _Connection, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "gateway.auth":
            token = str(params.get("token", ""))
            provider = self.auth_provider
            identity = provider.authenticate(token) if provider is not None else connection.identity
            if identity is None:
                raise GatewayRpcError(-32011, "authentication_failed")
            connection.authenticated = True
            connection.identity = identity
            return {
                "authenticated": True,
                "transport": "tcp_loopback",
                "identity": identity.to_public_dict(),
            }
        if method == "health":
            return self.runtime.health()
        if method == "capabilities":
            return {
                "methods": list(dict.fromkeys(["gateway.auth", *self.runtime.capabilities()])),
                "streaming": True,
            }
        if method == "session.create":
            return self.runtime.create_session(str(params.get("session_id", "")))
        if method == "session.resume":
            return self.runtime.resume_session(str(params.get("session_id", "")))
        if method == "session.close":
            return self.runtime.close_session(str(params.get("session_id", "")))
        if method == "run.start":
            return self.runtime.start_run(
                str(params.get("session_id", "")),
                str(params.get("message", "")),
                run_id=str(params.get("run_id", "")),
                busy_policy=str(params.get("busy_policy", "APPEND")),
                identity=connection.identity,
            )
        if method == "run.cancel":
            return self.runtime.cancel_run(
                str(params.get("run_id", "")), str(params.get("session_id", ""))
            )
        if method == "run.inject":
            return self.runtime.inject_run(
                str(params.get("session_id", "")),
                str(params.get("message", "")),
                identity=connection.identity,
            )
        if method == "run.interrupt":
            return self.runtime.interrupt_run(
                str(params.get("session_id", "")),
                str(params.get("message", "")),
                str(params.get("run_id", "")),
                identity=connection.identity,
            )
        if method == "interaction.resolve":
            return self.runtime.resolve_interaction(
                str(params.get("interaction_id", "")),
                params.get("value"),
                run_id=str(params.get("run_id", "")),
                session_id=str(params.get("session_id", "")),
            )
        if method in {"run.subscribe", "event.subscribe"}:
            session_id = str(params.get("session_id", "")).strip()
            run_filter = str(params.get("run_id", "")).strip()
            unsubscribe = self.runtime.subscribe(
                session_id,
                lambda event: connection.notify_from_thread(event, run_filter),
            )
            connection.unsubscribers.append(unsubscribe)
            return {"session_id": session_id, "subscribed": True, "run_id": run_filter}
        if method == "cron.create":
            session_id = str(params.get("session_id", ""))
            return self.runtime.cron_create(session_id, params)
        if method == "cron.list":
            return self.runtime.cron_list(str(params.get("session_id", "")))
        if method == "cron.cancel":
            return self.runtime.cron_cancel(
                str(params.get("session_id", "")), str(params.get("job_id", ""))
            )
        raise GatewayRpcError(-32601, "method_not_found", {"method": method})

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = _Connection(self, reader, writer)
        self._connections.add(connection)
        try:
            await connection.run()
        finally:
            self._connections.discard(connection)


__all__ = ["GatewayRpcError", "GatewayServer", "MAX_RPC_FRAME_BYTES"]
