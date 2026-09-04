"""Offline-first provider diagnostics with an explicit probe escape hatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from .provider_config import ProviderConfig
from .provider_registry import PROVIDER_REGISTRY, ProviderRegistry
from .security import URLSecurityError, validate_url


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    model: str
    endpoint: str
    status: str
    healthy: bool
    credential_present: bool
    endpoint_valid: bool
    probe_attempted: bool
    reason: str
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "status": self.status,
            "healthy": self.healthy,
            "credential_present": self.credential_present,
            "endpoint_valid": self.endpoint_valid,
            "probe_attempted": self.probe_attempted,
            "reason": self.reason,
            "error": self.error,
        }


def _validate_endpoint(endpoint: str, allow_private: bool, resolve_host: bool):
    try:
        validate_url(
            endpoint,
            allow_private=allow_private,
            resolve_host=resolve_host,
        )
    except URLSecurityError as exc:
        return False, str(exc)
    return True, ""


def check_provider(
    config: ProviderConfig,
    registry: ProviderRegistry = PROVIDER_REGISTRY,
    probe: bool = False,
    probe_fn: Callable[[], Any] | None = None,
    resolve_host: bool = False,
) -> ProviderHealth:
    """Validate configuration locally; network access happens only with probe."""

    spec = registry.get_spec(config.provider)
    endpoint = config.host if spec.client_kind == "ollama" else config.base_url
    endpoint_valid, endpoint_reason = _validate_endpoint(
        endpoint,
        allow_private=spec.client_kind == "ollama",
        resolve_host=resolve_host,
    )
    credential_present = bool(config.api_key)
    config_valid = bool(config.model and endpoint_valid)
    if spec.requires_api_key and not credential_present:
        config_valid = False
        reason = "missing configured API credential"
    elif not endpoint_valid:
        reason = endpoint_reason
    elif not config.model:
        reason = "model name is empty"
    else:
        reason = "configuration is valid; endpoint not probed"

    if not config_valid:
        return ProviderHealth(
            provider=spec.name,
            model=config.model,
            endpoint=endpoint,
            status="MISCONFIGURED",
            healthy=False,
            credential_present=credential_present,
            endpoint_valid=endpoint_valid,
            probe_attempted=False,
            reason=reason,
        )

    if not probe:
        return ProviderHealth(
            provider=spec.name,
            model=config.model,
            endpoint=endpoint,
            status="READY_UNPROBED",
            healthy=True,
            credential_present=credential_present,
            endpoint_valid=endpoint_valid,
            probe_attempted=False,
            reason=reason,
        )

    if probe_fn is None:
        return ProviderHealth(
            provider=spec.name,
            model=config.model,
            endpoint=endpoint,
            status="PROBE_NOT_CONFIGURED",
            healthy=False,
            credential_present=credential_present,
            endpoint_valid=endpoint_valid,
            probe_attempted=False,
            reason="explicit probe requested but no probe function was supplied",
        )

    try:
        probe_fn()
    except Exception as exc:  # diagnostics must return structured output
        from .provider_contract import classify_model_error

        classification = classify_model_error(exc, provider=spec.name)
        return ProviderHealth(
            provider=spec.name,
            model=config.model,
            endpoint=endpoint,
            status="PROBE_FAILED",
            healthy=False,
            credential_present=credential_present,
            endpoint_valid=endpoint_valid,
            probe_attempted=True,
            reason=str(exc),
            error=classification.to_dict(),
        )
    return ProviderHealth(
        provider=spec.name,
        model=config.model,
        endpoint=endpoint,
        status="PROBE_OK",
        healthy=True,
        credential_present=credential_present,
        endpoint_valid=endpoint_valid,
        probe_attempted=True,
        reason="provider probe completed successfully",
    )
