import asyncio
import json
from pathlib import Path

from codecub.gateway import GatewayServer
from codecub.gateway_runtime import EmbeddedRuntimeGateway
from codecub.models import FakeModelClient
from codecub.runtime import Pico
from codecub.sessions import SessionStore
from codecub.workspace import WorkspaceContext


def _agent_factory(root: Path):
    store = SessionStore(root / ".codecub" / "sessions")

    def factory(*, session_id=None, resume=False):
        model = FakeModelClient(["<final>gateway done</final>"])
        kwargs = {
            "model_client": model,
            "workspace": WorkspaceContext.build(root),
            "session_store": store,
            "approval_policy": "never",
        }
        if resume:
            kwargs["session"] = store.load(session_id)
        return Pico(**kwargs)

    return factory


async def _send(writer, payload):
    writer.write((json.dumps(payload) + "\n").encode("utf-8"))
    await writer.drain()


async def _read(reader):
    line = await asyncio.wait_for(reader.readline(), timeout=5)
    assert line
    return json.loads(line.decode("utf-8"))


def test_gateway_rpc_runs_through_spine_turn_runner_and_streams_events(tmp_path):
    async def scenario():
        runtime = EmbeddedRuntimeGateway(_agent_factory(tmp_path), user_workers=1, system_workers=1)
        server = GatewayServer(runtime, auth_token="secret", outbound_queue_size=32)
        await server.start()
        reader, writer = await asyncio.open_connection(*server.address)
        try:
            await _send(writer, {"jsonrpc": "2.0", "id": 1, "method": "gateway.auth", "params": {"token": "secret"}})
            assert (await _read(reader))["result"]["authenticated"] is True

            await _send(writer, {"jsonrpc": "2.0", "id": 2, "method": "session.create", "params": {}})
            session = (await _read(reader))["result"]
            session_id = session["session_id"]
            assert session["workspace"] == str(tmp_path.resolve())

            await _send(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "run.subscribe",
                    "params": {"session_id": session_id},
                },
            )
            assert (await _read(reader))["result"]["subscribed"] is True

            await _send(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "run.start",
                    "params": {"session_id": session_id, "message": "inspect the repository"},
                },
            )
            start_response = await _read(reader)
            run_id = start_response["result"]["run_id"]
            assert start_response["result"]["status"] == "QUEUED"

            events = []
            while not any(event.get("params", {}).get("event") == "run.completed" for event in events):
                frame = await _read(reader)
                if frame.get("method") == "run.event":
                    events.append(frame)
            completed = next(
                frame["params"] for frame in events if frame["params"]["event"] == "run.completed"
            )
            assert completed["run_id"] == run_id
            assert completed["payload"]["answer"] == "gateway done"
            assert any(frame["params"]["event"] == "run_status" for frame in events)
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_gateway_auth_gate_rejects_unauthenticated_requests(tmp_path):
    async def scenario():
        runtime = EmbeddedRuntimeGateway(_agent_factory(tmp_path), user_workers=1, system_workers=1)
        server = GatewayServer(runtime, auth_token="secret")
        await server.start()
        reader, writer = await asyncio.open_connection(*server.address)
        try:
            await _send(writer, {"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}})
            assert (await _read(reader))["error"]["message"] == "authentication_required"
            await _send(writer, {"jsonrpc": "2.0", "id": 2, "method": "gateway.auth", "params": {"token": "wrong"}})
            assert (await _read(reader))["error"]["message"] == "authentication_failed"
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_gateway_can_be_explicitly_local_unauthenticated_only(tmp_path):
    runtime = EmbeddedRuntimeGateway(_agent_factory(tmp_path), user_workers=1, system_workers=1)
    try:
        server = GatewayServer(runtime, allow_unauthenticated=True)
        assert server.auth_token is None
    finally:
        runtime.close()


def test_gateway_exposes_persistent_cron_control_plane(tmp_path):
    async def scenario():
        runtime = EmbeddedRuntimeGateway(_agent_factory(tmp_path), user_workers=1, system_workers=1)
        server = GatewayServer(runtime, auth_token="secret")
        await server.start()
        reader, writer = await asyncio.open_connection(*server.address)
        try:
            await _send(writer, {"jsonrpc": "2.0", "id": 1, "method": "gateway.auth", "params": {"token": "secret"}})
            assert (await _read(reader))["result"]["authenticated"] is True
            await _send(writer, {"jsonrpc": "2.0", "id": 2, "method": "session.create", "params": {}})
            session_id = (await _read(reader))["result"]["session_id"]
            await _send(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "cron.create",
                    "params": {
                        "session_id": session_id,
                        "id": "daily-check",
                        "message": "check status",
                        "cron": "0 0 1 1 *",
                    },
                },
            )
            created = (await _read(reader))["result"]
            assert created["id"] == "daily-check"
            await _send(writer, {"jsonrpc": "2.0", "id": 4, "method": "cron.list", "params": {"session_id": session_id}})
            assert (await _read(reader))["result"]["jobs"][0]["id"] == "daily-check"
            await _send(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "cron.cancel",
                    "params": {"session_id": session_id, "job_id": "daily-check"},
                },
            )
            assert (await _read(reader))["result"]["status"] == "cancelled"
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())
