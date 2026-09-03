import pytest
import threading
import time

from codecub.spine import BusyPolicy, Origin, ResourcePools, Run, RunStatus, Source, TurnRequest
from codecub.tracing import TraceContext, TraceRecorder
from codecub.spine import (
    CancellationSource, ControlMessage, DurableExecutionBroker, InMemoryControlBus,
    InteractionBroker, LegacyTurnRunner, RedisStreamControlBus, Spine,
)
from codecub.run_queue import LocalRunQueue


def test_turn_request_separates_origin_from_surface_source():
    request = TurnRequest("fix it", "s1", "c1", Origin.USER, Source(channel="desktop", extras={"tenant": "t1"}))
    assert request.origin is Origin.USER
    assert request.source.channel == "desktop"
    assert request.source.extras == {"tenant": "t1"}
    assert request.busy_policy is BusyPolicy.APPEND
    assert request.trace_id


def test_cancel_is_not_a_busy_policy_and_terminal_run_cannot_restart():
    with pytest.raises(ValueError):
        BusyPolicy("CANCEL")
    run = Run("r1", "t1", "c1", "s1", "trace1")
    run.transition_to(RunStatus.RUNNING)
    run.transition_to(RunStatus.CANCEL_REQUESTED)
    run.transition_to(RunStatus.CANCELLING)
    run.transition_to(RunStatus.CANCELLED)
    with pytest.raises(ValueError):
        run.transition_to(RunStatus.RUNNING)
    with pytest.raises(ValueError):
        Run("r2", "t2", "c1", "s1", "trace2").transition_to(RunStatus.COMPLETED)
    with pytest.raises(ValueError):
        ControlMessage("UNKNOWN", "run")


def test_trace_recorder_keeps_all_correlation_identifiers():
    received = []
    context = TraceContext("trace", "session", "conversation", "turn", "run")
    event = TraceRecorder(received.append).record("turn.submitted", context, {"origin": "USER"})
    assert received == [event]
    assert event.to_dict()["conversation_id"] == "conversation"


def test_same_conversation_is_serialized_but_different_conversations_overlap():
    active, maximum, lock = 0, 0, threading.Lock()

    class Runner:
        def run(self, request, run):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(request.turn_id, run.run_id, RunStatus.COMPLETED, request.message)

    spine = Spine(Runner())
    one = spine.submit(TurnRequest("one", "s", "same"))
    two = spine.submit(TurnRequest("two", "s", "same"))
    three = spine.submit(TurnRequest("three", "s", "other"))
    assert [item.result(timeout=1).answer for item in (one, two, three)] == ["one", "two", "three"]
    assert maximum == 2
    spine.pools.shutdown()


def test_one_hundred_conversations_with_ten_turns_have_no_loss_or_overlap():
    active_by_conversation, maximum_by_conversation = {}, {}
    active_total, maximum_total = 0, 0
    lock = threading.Lock()

    class Runner:
        def run(self, request, run):
            nonlocal active_total, maximum_total
            with lock:
                conversation = request.conversation_id
                active_by_conversation[conversation] = active_by_conversation.get(conversation, 0) + 1
                maximum_by_conversation[conversation] = max(
                    maximum_by_conversation.get(conversation, 0), active_by_conversation[conversation]
                )
                active_total += 1
                maximum_total = max(maximum_total, active_total)
            time.sleep(0.001)
            with lock:
                active_by_conversation[request.conversation_id] -= 1
                active_total -= 1
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(
                request.turn_id, run.run_id, RunStatus.COMPLETED, request.message
            )

    spine = Spine(Runner())
    futures = [
        spine.submit(TurnRequest(f"c{conversation}-t{turn}", "s", f"c{conversation}"))
        for conversation in range(100)
        for turn in range(10)
    ]
    outcomes = [future.result(timeout=10) for future in futures]
    assert len(outcomes) == 1000
    assert all(outcome.status is RunStatus.COMPLETED for outcome in outcomes)
    assert all(maximum == 1 for maximum in maximum_by_conversation.values())
    assert maximum_total > 1
    spine.pools.shutdown()


def test_user_pool_remains_available_when_system_pool_is_saturated():
    system_started = threading.Event()
    release_system = threading.Event()

    class Runner:
        def run(self, request, run):
            del run
            if request.origin is Origin.CRON:
                system_started.set()
                release_system.wait(1)
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(
                request.turn_id, "run", RunStatus.COMPLETED, request.message
            )

    spine = Spine(Runner(), pools=ResourcePools(user_workers=1, system_workers=1))
    blocked = spine.submit(TurnRequest("system-one", "s", "system-one", origin=Origin.CRON))
    assert system_started.wait(1)
    queued_system = spine.submit(TurnRequest("system-two", "s", "system-two", origin=Origin.SUBAGENT))
    user = spine.submit(TurnRequest("user", "s", "user", origin=Origin.USER))
    assert user.result(timeout=1).answer == "user"
    release_system.set()
    assert blocked.result(timeout=1).status is RunStatus.COMPLETED
    assert queued_system.result(timeout=1).status is RunStatus.COMPLETED
    spine.pools.shutdown()


def test_inject_is_live_only_and_system_controls_are_demoted():
    started = threading.Event()
    release = threading.Event()
    events = []

    class Runner:
        def run(self, request, run):
            started.set()
            release.wait(1)
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(request.turn_id, run.run_id, RunStatus.COMPLETED)

    spine = Spine(Runner(), trace_emit=events.append)
    active = spine.submit(TurnRequest("long", "s", "c"))
    assert started.wait(1)
    injected = spine.submit(TurnRequest("constraint", "s", "c", busy_policy=BusyPolicy.INJECT))
    assert injected is None
    assert [item.message for item in spine.drain_injections("c")] == ["constraint"]
    queued = spine.submit(TurnRequest("system", "s", "c", origin=Origin.CRON, busy_policy=BusyPolicy.INJECT))
    assert queued is not None
    cron_interrupt = spine.submit(TurnRequest("cron-interrupt", "s", "c", origin=Origin.CRON, busy_policy=BusyPolicy.INTERRUPT))
    subagent_inject = spine.submit(TurnRequest("subagent-inject", "s", "c", origin=Origin.SUBAGENT, busy_policy=BusyPolicy.INJECT))
    assert cron_interrupt is not None and subagent_inject is not None
    assert any(event.payload.get("demotion_reason") == "origin_not_authorized" for event in events)
    release.set()
    assert active.result(timeout=1).status is RunStatus.COMPLETED
    assert queued.result(timeout=1).status is RunStatus.COMPLETED
    assert cron_interrupt.result(timeout=1).status is RunStatus.COMPLETED
    assert subagent_inject.result(timeout=1).status is RunStatus.COMPLETED
    demoted = [event for event in events if event.payload.get("demotion_reason") == "origin_not_authorized"]
    assert {(event.payload["requested_policy"], event.payload["applied_policy"]) for event in demoted} >= {
        ("INJECT", "APPEND"),
        ("INTERRUPT", "APPEND"),
    }
    spine.pools.shutdown()


@pytest.mark.parametrize(
    ("origin", "requested", "applied", "reason"),
    [
        (Origin.USER, BusyPolicy.APPEND, BusyPolicy.APPEND, ""),
        (Origin.USER, BusyPolicy.INJECT, BusyPolicy.INJECT, ""),
        (Origin.USER, BusyPolicy.INTERRUPT, BusyPolicy.INTERRUPT, ""),
        (Origin.CRON, BusyPolicy.APPEND, BusyPolicy.APPEND, ""),
        (Origin.CRON, BusyPolicy.INJECT, BusyPolicy.APPEND, "origin_not_authorized"),
        (Origin.CRON, BusyPolicy.INTERRUPT, BusyPolicy.APPEND, "origin_not_authorized"),
        (Origin.SUBAGENT, BusyPolicy.APPEND, BusyPolicy.APPEND, ""),
        (Origin.SUBAGENT, BusyPolicy.INJECT, BusyPolicy.APPEND, "origin_not_authorized"),
        (Origin.SUBAGENT, BusyPolicy.INTERRUPT, BusyPolicy.APPEND, "origin_not_authorized"),
    ],
)
def test_control_authority_matrix(origin, requested, applied, reason):
    request = TurnRequest("work", "session", "conversation", origin=origin, busy_policy=requested)
    assert Spine._applied_policy(request) == (applied, reason)


def test_undrained_injection_is_appended_without_loss():
    started = threading.Event()
    release = threading.Event()
    executed, events = [], []

    class Runner:
        def run(self, request, run):
            executed.append(request.message)
            if request.message == "long":
                started.set()
                release.wait(1)
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(
                request.turn_id, run.run_id, RunStatus.COMPLETED
            )

    spine = Spine(Runner(), trace_emit=events.append)
    active = spine.submit(TurnRequest("long", "s", "c"))
    assert started.wait(1)
    assert spine.submit(TurnRequest("constraint", "s", "c", busy_policy=BusyPolicy.INJECT)) is None
    release.set()
    assert active.result(timeout=1).status is RunStatus.COMPLETED
    deadline = time.monotonic() + 1
    while "constraint" not in executed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert executed == ["long", "constraint"]
    assert any(event.name == "turn.inject_fallback_appended" for event in events)
    spine.pools.shutdown()


def test_interrupt_requests_cancellation_and_runs_replacement_next():
    started = threading.Event()
    release = threading.Event()
    executed = []

    class Runner:
        def run(self, request, run):
            executed.append(request.message)
            if request.message == "long":
                started.set()
                while not request.runtime_extensions["cancellation_token"].cancelled:
                    release.wait(0.01)
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(
                request.turn_id, run.run_id, RunStatus.COMPLETED
            )

    spine = Spine(Runner())
    active = spine.submit(TurnRequest("long", "s", "c"))
    assert started.wait(1)
    replacement = spine.submit(TurnRequest("replacement", "s", "c", busy_policy=BusyPolicy.INTERRUPT))
    assert active.result(timeout=1).status is RunStatus.CANCELLED
    assert replacement.result(timeout=1).status is RunStatus.COMPLETED
    assert executed == ["long", "replacement"]
    spine.pools.shutdown()


def test_one_thousand_undrained_injections_are_all_fallback_appended():
    started = threading.Event()
    release = threading.Event()
    fallback_finished = threading.Event()
    fallback_count = 0
    count_lock = threading.Lock()

    class Runner:
        def run(self, request, run):
            nonlocal fallback_count
            if request.message.startswith("long-"):
                started.set()
                release.wait(1)
            else:
                with count_lock:
                    fallback_count += 1
                fallback_finished.set()
            return __import__("codecub.spine", fromlist=["TurnOutcome"]).TurnOutcome(
                request.turn_id, run.run_id, RunStatus.COMPLETED
            )

    spine = Spine(Runner())
    for index in range(1000):
        active = spine.submit(TurnRequest(f"long-{index}", "s", "c"))
        assert started.wait(1)
        assert spine.submit(TurnRequest(f"constraint-{index}", "s", "c", busy_policy=BusyPolicy.INJECT)) is None
        release.set()
        assert active.result(timeout=1).status is RunStatus.COMPLETED
        assert fallback_finished.wait(1)
        started.clear()
        release.clear()
        fallback_finished.clear()
    assert fallback_count == 1000
    stats = spine._lanes["c"].injection_stats()
    assert stats == {"submitted": 1000, "drained": 0, "fallback_appended": 1000}
    assert stats["submitted"] == stats["drained"] + stats["fallback_appended"]
    spine.pools.shutdown()


def test_legacy_runner_binds_lane_injection_provider_to_new_agent():
    captured = {}

    class Agent:
        injection_provider = None

        def ask(self, message, run_id):
            captured["injected"] = [item.message for item in self.injection_provider()]
            return message

    spine = Spine(LegacyTurnRunner(lambda _request: Agent()))
    future = spine.submit(TurnRequest("work", "s", "c"))
    assert future.result(timeout=1).answer == "work"
    assert captured["injected"] == []
    spine.pools.shutdown()


def test_legacy_runner_binds_a_runtime_compatible_cancellation_checker():
    captured = {}

    class Agent:
        def ask(self, message, run_id):
            captured["cancelled"] = self.cancel_checker(self, object())
            return message

    spine = Spine(LegacyTurnRunner(lambda _request: Agent()))
    outcome = spine.submit(TurnRequest("work", "s", "c")).result(timeout=1)
    assert outcome.status is RunStatus.COMPLETED
    assert captured["cancelled"] is False
    spine.pools.shutdown()


def test_cancellation_and_interaction_contracts_are_idempotent():
    source = CancellationSource()
    seen = []
    source.token.on_cancel(lambda: seen.append("cancelled"))
    assert source.cancel()
    assert not source.cancel()
    assert source.token.cancelled and seen == ["cancelled"]
    broker = InteractionBroker()
    request = broker.request("approval", "run", {"tool": "patch_file"})
    assert broker.resolve(request.interaction_id, True)
    assert not broker.resolve(request.interaction_id, False)
    assert broker.wait(request, timeout=0.1) is True


def test_interaction_broker_rejects_wrong_run_and_cleans_up_timeout_and_cancel():
    broker = InteractionBroker()
    request = broker.request("approval", "run-a")
    assert not broker.resolve(request.interaction_id, True, run_id="run-b")
    assert broker.cancel_run("run-b") == 0
    assert broker.cancel_run("run-a") == 1
    assert broker.wait(request, timeout=0.1) is False
    timed_out = broker.request("question", "run-timeout")
    with pytest.raises(TimeoutError):
        broker.wait(timed_out, timeout=0.001)
    assert not broker.resolve(timed_out.interaction_id, "late", run_id="run-timeout")


def test_legacy_runner_propagates_spine_trace_tuple_to_runtime():
    captured = {}

    class Agent:
        def ask(self, message, run_id):
            captured.update(self.spine_trace_context)
            return message

    request = TurnRequest("work", "session", "conversation")
    outcome = Spine(LegacyTurnRunner(lambda _request: Agent())).submit(request).result(timeout=1)
    assert outcome.status is RunStatus.COMPLETED
    assert captured == {
        "trace_id": request.trace_id,
        "session_id": "session",
        "conversation_id": "conversation",
        "turn_id": request.turn_id,
        "run_id": outcome.run_id,
    }


def test_durable_broker_reuses_queue_and_control_delivery_is_idempotent():
    request = TurnRequest("inspect", "s", "c")
    run = Run("r", request.turn_id, "c", "s", request.trace_id)
    queue = LocalRunQueue()
    assert DurableExecutionBroker(queue).submit(request, run)
    envelope = queue.dequeue()
    assert envelope["conversation_id"] == "c" and envelope["trace_id"] == request.trace_id
    handled = []
    control = ControlMessage("RUN_INJECT", "r", turn_id=request.turn_id, session_id="s", conversation_id="c", trace_id=request.trace_id, payload={"message": "stop"})
    bus = InMemoryControlBus()
    assert bus.apply_once(control, handled.append)
    assert not bus.apply_once(control, handled.append)
    assert handled == [control]


def test_spine_enqueues_a_durable_turn_without_breaking_lane_completion():
    queue = LocalRunQueue()
    spine = Spine(execution_broker=DurableExecutionBroker(queue))
    request = TurnRequest("inspect", "s", "c")
    outcome = spine.submit(request).result(timeout=1)
    assert outcome.status is RunStatus.QUEUED
    queued = queue.dequeue()
    assert queued["turn_id"] == request.turn_id
    assert queued["conversation_id"] == "c"
    spine.pools.shutdown()


def test_redis_control_bus_handles_a_redelivered_control_once():
    class Redis:
        def __init__(self):
            self.keys = set()
            self.messages = []

        def set(self, key, value, nx=False):
            del value
            if nx and key in self.keys:
                return False
            self.keys.add(key)
            return True

        def xadd(self, stream, fields):
            self.messages.append((stream, fields))
            return "1-0"

        def xgroup_create(self, *args, **kwargs):
            del args, kwargs

    redis = Redis()
    bus = RedisStreamControlBus(redis)
    control = ControlMessage("RUN_CANCEL", "run")
    seen = []
    assert bus.publish(control) == "1-0"
    assert bus.apply_once(control, seen.append)
    assert not bus.apply_once(control, seen.append)
    assert seen == [control]
    second_worker = RedisStreamControlBus(redis, consumer="worker-2")
    second_seen = []
    assert second_worker.apply_once(control, second_seen.append)
    assert second_seen == [control]


def test_redis_control_bus_consumes_and_acks_one_control_message():
    class Redis:
        def __init__(self):
            self.entries, self.acks = [], []

        def xgroup_create(self, *args, **kwargs):
            del args, kwargs

        def xadd(self, stream, fields):
            self.entries.append(("1-0", fields))
            return "1-0"

        def xreadgroup(self, group, consumer, streams, count, block):
            del group, consumer, streams, count, block
            return [("codecub:controls", self.entries[:1])] if self.entries else []

        def xack(self, stream, group, message_id):
            self.acks.append((stream, group, message_id))

        def set(self, *args, **kwargs):
            del args, kwargs
            return True

    redis = Redis()
    bus = RedisStreamControlBus(redis)
    control = ControlMessage("RUN_INJECT", "run", payload={"message": "constraint"})
    bus.publish(control)
    message_id, consumed = bus.consume_one()
    assert message_id == "1-0" and consumed == control
    bus.ack(message_id)
    assert redis.acks == [("codecub:controls", "codecub-controls:worker-1", "1-0")]
