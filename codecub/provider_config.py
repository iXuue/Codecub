"""Pure provider configuration layering for CLI and desktop callers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .model_gateway import GatewayPolicy
from .provider_registry import PROVIDER_REGISTRY, ProviderRegistry


def _first_env(environ: Mapping[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(environ.get(name, "")).strip()
        if value:
            return value
    return ""


def _arg_value(args, name: str, default=None):
    value = getattr(args, name, default)
    return value if value not in (None, "") else default


def _positive_int(value: str | int | None, name: str, default: int) -> int:
    try:
        parsed = int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _nonnegative_float(value: str | float | None, name: str, default: float) -> float:
    try:
        parsed = float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


@dataclass(frozen=True)
class ProviderConfig:
    """Resolved non-secret and secret runtime inputs for one provider."""

    provider: str
    model: str
    base_url: str
    api_key: str
    host: str
    temperature: float
    top_p: float
    timeout: int
    fallback_models: tuple[str, ...] = ()
    gateway_policy: GatewayPolicy = GatewayPolicy()

    @classmethod
    def from_args(
        cls,
        args,
        environ: Mapping[str, str] | None = None,
        registry: ProviderRegistry = PROVIDER_REGISTRY,
    ) -> "ProviderConfig":
        env = environ if environ is not None else os.environ
        spec = registry.resolve(
            _arg_value(args, "provider"),
            env.get("CODECUB_PROVIDER", ""),
        )
        model = str(
            _arg_value(args, "model")
            or _first_env(env, spec.model_env)
            or spec.default_model
        ).strip()
        base_url = str(
            _arg_value(args, "base_url")
            or _first_env(env, spec.base_url_env)
            or spec.default_base_url
        ).strip()
        host = str(
            _arg_value(args, "host")
            or _first_env(env, spec.host_env)
            or spec.default_base_url
        ).strip()
        api_key = _first_env(env, spec.api_key_envs)
        timeout_name = "ollama_timeout" if spec.client_kind == "ollama" else "openai_timeout"
        timeout_value = getattr(args, timeout_name, None)
        if timeout_value in (None, ""):
            timeout_value = getattr(args, "ollama_timeout", 300)
        timeout = _positive_int(timeout_value, timeout_name, 300)
        fallback_value = str(env.get("CODECUB_FALLBACK_MODELS", "")).strip()
        if not fallback_value:
            fallback_value = str(env.get("CODECUB_FALLBACK_MODEL", "")).strip()
        fallbacks = tuple(item.strip() for item in fallback_value.split(",") if item.strip())
        max_concurrency = _positive_int(
            env.get("CODECUB_MODEL_MAX_CONCURRENCY", "2"),
            "CODECUB_MODEL_MAX_CONCURRENCY",
            2,
        )
        policy = GatewayPolicy(
            max_concurrency=max_concurrency,
            min_interval_seconds=_nonnegative_float(
                env.get("CODECUB_MODEL_MIN_INTERVAL_SECONDS", "0"),
                "CODECUB_MODEL_MIN_INTERVAL_SECONDS",
                0.0,
            ),
            max_retries=_nonnegative_int(
                env.get("CODECUB_MODEL_MAX_RETRIES", "2"),
                "CODECUB_MODEL_MAX_RETRIES",
                2,
            ),
            retry_base_seconds=_nonnegative_float(
                env.get("CODECUB_MODEL_RETRY_BASE_SECONDS", "1"),
                "CODECUB_MODEL_RETRY_BASE_SECONDS",
                1.0,
            ),
        )
        return cls(
            provider=spec.name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            host=host,
            temperature=float(_arg_value(args, "temperature", 0.2)),
            top_p=float(_arg_value(args, "top_p", 0.9)),
            timeout=timeout,
            fallback_models=fallbacks,
            gateway_policy=policy,
        )

    def to_public_dict(self) -> dict:
        """Serialize diagnostics without exposing the API key."""

        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "host": self.host,
            "credential_present": bool(self.api_key),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "timeout": self.timeout,
            "fallback_models": list(self.fallback_models),
            "gateway_policy": {
                "max_concurrency": self.gateway_policy.max_concurrency,
                "min_interval_seconds": self.gateway_policy.min_interval_seconds,
                "max_retries": self.gateway_policy.max_retries,
                "retry_base_seconds": self.gateway_policy.retry_base_seconds,
            },
        }


def _nonnegative_int(value: str | int | None, name: str, default: int) -> int:
    try:
        parsed = int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed
