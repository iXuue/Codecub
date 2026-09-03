from io import BytesIO
import time
from urllib.error import HTTPError, URLError

import pytest

from codecub import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from codecub.model_gateway import GatewayPolicy, ModelGateway, is_transient_model_error
from codecub.code_index import CodeIndex
from codecub.retrieval import HybridRetriever
from codecub.resilience import ToolCircuitBreaker


class FlakyClient:
    model = "fake-primary"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def complete_with_tools(self, *_args, **_kwargs):
        return self.complete()

    def stream_complete(self, *_args, **_kwargs):
        return self.complete()


def retry_gateway(client, fallback=None):
    return ModelGateway(
        client,
        GatewayPolicy(max_retries=1, retry_base_seconds=0),
        fallback=fallback,
    )


def test_gateway_retries_429():
    error = HTTPError("https://example.test", 429, "rate limited", {}, BytesIO())
    client = FlakyClient([error, "ok"])
    assert retry_gateway(client).complete([]) == "ok"
    assert client.calls == 2


def test_gateway_retries_timeout():
    client = FlakyClient([TimeoutError("timeout"), "ok"])
    assert retry_gateway(client).complete([]) == "ok"
    assert client.calls == 2


@pytest.mark.parametrize("code", [400, 401, 403])
def test_http_permanent_client_errors_do_not_retry(code):
    error = HTTPError("https://example.test", code, "client error", {}, BytesIO())
    client = FlakyClient([error, "must-not-run"])
    with pytest.raises(HTTPError):
        retry_gateway(client).complete([])
    assert client.calls == 1
    assert is_transient_model_error(error) is False


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503])
def test_http_transient_statuses_are_retryable(code):
    error = HTTPError("https://example.test", code, "temporary", {}, BytesIO())
    assert is_transient_model_error(error) is True


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timeout"), ConnectionResetError("reset"), URLError("network down")],
)
def test_transport_errors_are_retryable(error):
    assert is_transient_model_error(error) is True


def test_unknown_exception_is_not_retryable():
    assert is_transient_model_error(RuntimeError("permanent failure")) is False


def test_gateway_uses_fallback_after_exhaustion():
    primary = FlakyClient([TimeoutError("timeout"), TimeoutError("timeout")])
    fallback = FlakyClient(["fallback-ok"])
    assert retry_gateway(primary, fallback).complete([]) == "fallback-ok"
    assert primary.calls == 2
    assert fallback.calls == 1


def test_gateway_applies_throttle_and_all_completion_shapes():
    client = FlakyClient(["one", "two", "three"])
    gateway = ModelGateway(
        client, GatewayPolicy(min_interval_seconds=0.01, max_retries=0)
    )
    began = time.monotonic()
    assert gateway.complete("prompt", 1) == "one"
    assert gateway.complete_with_tools([], [], 1) == "two"
    assert gateway.stream_complete("prompt", 1, on_delta=lambda _text: None) == "three"
    assert time.monotonic() - began >= 0.01


def test_breaker_opens_then_recovers_half_open():
    clock = [0.0]
    breaker = ToolCircuitBreaker(failure_threshold=2, reset_seconds=10, clock=lambda: clock[0])
    breaker.record_failure("search")
    breaker.record_failure("search")
    assert breaker.status("search") == "open"
    assert not breaker.allow("search")
    clock[0] = 10.0
    assert breaker.status("search") == "half_open"
    assert breaker.allow("search")
    assert not breaker.allow("search")
    breaker.record_success("search")
    assert breaker.status("search") == "closed"


def test_non_idempotent_patch_is_invoked_once_after_failure(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = MiniAgent(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )
    calls = []

    def fail_once(_args):
        calls.append(1)
        raise TimeoutError("tool timeout")

    agent.tools["patch_file"]["run"] = fail_once
    assert "failed" in agent.run_tool(
        "patch_file", {"path": "README.md", "old_text": "demo", "new_text": "done"}
    )
    assert calls == [1]


def test_retrieval_falls_back_when_embedding_fails(tmp_path):
    (tmp_path / "example.py").write_text("def target():\n    return 1\n", encoding="utf-8")

    class BrokenEmbedding:
        def embed(self, _text):
            raise TimeoutError("embedding timeout")

    index = CodeIndex(tmp_path)
    index.refresh()
    result = HybridRetriever(
        tmp_path, index, embedding_client=BrokenEmbedding(), reranker=False
    ).retrieve("target", limit=5)
    assert result.strategy == "lexical_ast_rrf"
    assert result.hits
    assert any(item["reason"] == "semantic_error" for item in result.filtered_out)
