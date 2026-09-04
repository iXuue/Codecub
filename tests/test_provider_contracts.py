from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

from codecub import (
    ErrorKind,
    ProviderConfig,
    ProviderRegistry,
    PROVIDER_REGISTRY,
    check_provider,
    classify_model_error,
)
from codecub.cli import main
from codecub.model_gateway import GatewayPolicy, ModelGateway


def cli_args(**overrides):
    values = {
        "provider": None,
        "model": None,
        "base_url": None,
        "host": None,
        "temperature": 0.2,
        "top_p": 0.9,
        "openai_timeout": 30,
        "ollama_timeout": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_provider_registry_describes_all_existing_providers_without_secret_values():
    assert tuple(PROVIDER_REGISTRY) == (
        "ollama",
        "openai",
        "deepseek",
        "kimi",
        "minimax",
        "anthropic",
    )
    catalog = PROVIDER_REGISTRY.public_catalog()
    assert all("sk-" not in str(item).lower() for item in catalog)
    assert PROVIDER_REGISTRY.match_model("deepseek-chat").name == "deepseek"
    assert PROVIDER_REGISTRY.match_model("not-a-known-prefix") is None


def test_provider_registry_rejects_duplicate_names():
    spec = PROVIDER_REGISTRY["openai"]
    try:
        ProviderRegistry((spec, spec))
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate provider names must be rejected")


def test_provider_config_uses_explicit_then_environment_then_catalog_defaults():
    args = cli_args(provider="deepseek", model="explicit-model", base_url="https://explicit.test")
    config = ProviderConfig.from_args(
        args,
        {
            "DEEPSEEK_MODEL": "env-model",
            "DEEPSEEK_API_BASE": "https://env.test",
            "DEEPSEEK_API_KEY": "secret-value",
            "CODECUB_FALLBACK_MODELS": "backup-a, backup-b",
            "CODECUB_MODEL_MAX_RETRIES": "3",
        },
    )
    assert config.provider == "deepseek"
    assert config.model == "explicit-model"
    assert config.base_url == "https://explicit.test"
    assert config.api_key == "secret-value"
    assert config.fallback_models == ("backup-a", "backup-b")
    assert config.gateway_policy.max_retries == 3
    public = config.to_public_dict()
    assert "secret-value" not in str(public)
    assert public["credential_present"] is True


def test_provider_config_uses_registry_defaults_and_rejects_bad_policy():
    config = ProviderConfig.from_args(cli_args(provider="ollama"), {})
    assert config.model == "qwen3.5:4b"
    assert config.host == "http://127.0.0.1:11434"

    try:
        ProviderConfig.from_args(
            cli_args(provider="ollama"),
            {"CODECUB_MODEL_MAX_CONCURRENCY": "0"},
        )
    except ValueError as exc:
        assert "greater than zero" in str(exc)
    else:
        raise AssertionError("invalid policy must be rejected")


def test_provider_health_is_offline_by_default_and_probe_is_explicit():
    config = ProviderConfig.from_args(
        cli_args(provider="deepseek"),
        {"DEEPSEEK_API_KEY": "secret-value"},
    )
    calls = []
    health = check_provider(config, probe_fn=lambda: calls.append("called"))
    assert health.status == "READY_UNPROBED"
    assert health.probe_attempted is False
    assert calls == []

    health = check_provider(
        config,
        probe=True,
        probe_fn=lambda: calls.append("called"),
    )
    assert health.status == "PROBE_OK"
    assert health.probe_attempted is True
    assert calls == ["called"]


def test_provider_health_reports_missing_credential_without_network():
    config = ProviderConfig.from_args(cli_args(provider="openai"), {})
    health = check_provider(config)
    assert health.status == "MISCONFIGURED"
    assert health.healthy is False
    assert health.probe_attempted is False


def test_error_classification_preserves_permanent_http_behavior():
    error = HTTPError("https://example.test", 401, "Unauthorized", {}, BytesIO())
    classification = classify_model_error(error, provider="openai")
    assert classification.kind is ErrorKind.AUTH
    assert classification.retryable is False
    assert classification.fallback_eligible is False

    error = HTTPError("https://example.test", 429, "rate limited", {}, BytesIO())
    classification = classify_model_error(error, provider="openai")
    assert classification.kind is ErrorKind.RATE_LIMIT
    assert classification.retryable is True
    assert classification.fallback_eligible is True


class Client:
    def __init__(self, model, outcomes):
        self.model = model
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_gateway_supports_multiple_fallbacks_and_structured_metadata():
    primary = Client("primary", [RuntimeError("model not found")])
    first = Client("first", [RuntimeError("quota exceeded")])
    second = Client("second", ["ok"])
    gateway = ModelGateway(
        primary,
        GatewayPolicy(max_retries=0, retry_base_seconds=0),
        fallbacks=(first, second),
    )
    assert gateway.complete("prompt", 1) == "ok"
    assert (primary.calls, first.calls, second.calls) == (1, 1, 1)
    assert gateway.last_completion_metadata["fallback_used"] is True
    assert gateway.last_completion_metadata["fallback_chain_index"] == 2
    assert gateway.last_completion_metadata["last_error_classification"]["kind"] == "billing"


def test_cli_doctor_does_not_construct_or_call_model_by_default(tmp_path, capsys):
    result = main(["--doctor", "--cwd", str(tmp_path), "--provider", "ollama"])
    assert result == 0
    output = capsys.readouterr().out
    assert '"probe_attempted": false' in output
    assert '"status": "READY_UNPROBED"' in output
