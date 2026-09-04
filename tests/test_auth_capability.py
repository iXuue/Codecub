import time

import pytest

from codecub.auth import (
    AuthError,
    AuthMiddleware,
    CapabilityPolicy,
    FakeAuthProvider,
    HmacAuthProvider,
    Identity,
    SignedAuthToken,
)
from codecub.models import FakeModelClient
from codecub.runtime import Pico
from codecub.sessions import SessionStore
from codecub.workspace import WorkspaceContext


def test_hmac_auth_token_round_trip_and_expiry():
    provider = HmacAuthProvider("test-secret")
    token = provider.issue(
        SignedAuthToken("researcher", ("tool:read",), expires_at=int(time.time()) + 60)
    )
    identity = provider.authenticate(token)
    assert identity is not None
    assert identity.subject == "researcher"
    assert identity.has_scope("tool:read")
    assert provider.authenticate(token + "x") is None

    expired = provider.issue(SignedAuthToken("old", ("tool:*",), expires_at=int(time.time()) - 1))
    assert provider.authenticate(expired) is None


def test_auth_middleware_requires_bearer_and_supports_fake_provider():
    identity = Identity("fake-user", frozenset({"run:start"}), auth_method="fake")
    middleware = AuthMiddleware(FakeAuthProvider({"credential": identity}))
    assert middleware.authenticate("Bearer credential") == identity
    with pytest.raises(AuthError, match="bearer"):
        middleware.authenticate("Basic credential")
    with pytest.raises(AuthError, match="rejected"):
        middleware.authenticate("Bearer wrong")


def test_capability_policy_is_enforced_at_tool_executor(tmp_path):
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    policy = CapabilityPolicy(
        default_deny=True,
        identity=Identity("researcher", frozenset({"tool:read"}), auth_method="test"),
    )
    agent = Pico(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        capability_policy=policy,
    )

    assert "ok" in agent.run_tool("read_file", {"path": "README.md", "start": 1, "end": 1})
    assert agent.run_tool("write_file", {"path": "nope.txt", "content": "blocked"}) == (
        "error: capability denied for write_file"
    )
    assert not (tmp_path / "nope.txt").exists()
    assert agent._last_tool_result_metadata["security_event_type"] == "capability_denied"
