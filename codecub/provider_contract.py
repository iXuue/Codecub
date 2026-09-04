"""Provider contracts shared by configuration, routing, and health checks.

The runtime-facing model client interface remains intentionally small.  This
module adds the metadata and error vocabulary around that interface without
making the existing HTTP clients inherit from a new framework hierarchy.
"""

from __future__ import annotations

import re
import socket
import urllib.error
from dataclasses import dataclass, field
from enum import Enum
from http.client import RemoteDisconnected
from typing import Any


class ErrorKind(str, Enum):
    """Stable categories used by the gateway for retry/fallback decisions."""

    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    NETWORK = "network"
    AUTH = "auth"
    BILLING = "billing"
    INVALID_REQUEST = "invalid_request"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorClassification:
    """Provider error semantics independent of a concrete HTTP SDK."""

    kind: ErrorKind
    retryable: bool = False
    fallback_eligible: bool = False
    status_code: int | None = None
    reason: str = ""
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "retryable": self.retryable,
            "fallback_eligible": self.fallback_eligible,
            "status_code": self.status_code,
            "reason": self.reason,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities exposed to orchestration without inspecting client types."""

    protocol: str
    supports_streaming: bool = True
    supports_native_tools: bool = False
    supports_prompt_cache: bool = False
    supports_tool_choice: bool = False
    supports_parallel_tool_calls: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "supports_streaming": self.supports_streaming,
            "supports_native_tools": self.supports_native_tools,
            "supports_prompt_cache": self.supports_prompt_cache,
            "supports_tool_choice": self.supports_tool_choice,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
        }


@dataclass(frozen=True)
class ProviderSpec:
    """Non-secret catalog entry for one supported provider."""

    name: str
    display_name: str
    client_kind: str
    default_model: str
    default_base_url: str
    model_env: tuple[str, ...] = ()
    base_url_env: tuple[str, ...] = ()
    api_key_envs: tuple[str, ...] = ()
    host_env: tuple[str, ...] = ()
    requires_api_key: bool = False
    model_prefixes: tuple[str, ...] = ()
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(protocol="unknown")
    )

    def to_dict(self) -> dict[str, Any]:
        """Return public metadata only; credential names are not values."""

        return {
            "name": self.name,
            "display_name": self.display_name,
            "client_kind": self.client_kind,
            "default_model": self.default_model,
            "default_base_url": self.default_base_url,
            "model_env": list(self.model_env),
            "base_url_env": list(self.base_url_env),
            "api_key_envs": list(self.api_key_envs),
            "host_env": list(self.host_env),
            "requires_api_key": self.requires_api_key,
            "model_prefixes": list(self.model_prefixes),
            "capabilities": self.capabilities.to_dict(),
        }


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(8):
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(exc: BaseException) -> int | None:
    for item in _exception_chain(exc):
        if isinstance(item, urllib.error.HTTPError):
            return int(item.code)
        for pattern in (
            r"\bHTTP(?:\s+error)?\s+(\d{3})\b",
            r"\bstatus(?:_code)?[=: ]+(\d{3})\b",
        ):
            match = re.search(pattern, str(item), re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def classify_model_error(exc: BaseException, provider: str = "") -> ErrorClassification:
    """Classify common provider failures without depending on an SDK.

    The classification is deliberately conservative: unknown errors are not
    retried or sent to a fallback.  Model-not-found errors may use a fallback
    model, but are not retried against the same endpoint.
    """

    status = _status_code(exc)
    text = " ".join(str(item) for item in _exception_chain(exc)).lower()

    if status in {401, 403} or any(
        token in text
        for token in (
            "unauthorized",
            "forbidden",
            "invalid api key",
            "invalid_api_key",
            "authentication failed",
            "authentication error",
        )
    ):
        return ErrorClassification(
            ErrorKind.AUTH,
            status_code=status,
            reason="provider rejected credentials or authorization",
            provider=provider,
        )

    if status == 402 or any(
        token in text
        for token in ("billing", "insufficient_quota", "payment required", "quota exceeded")
    ):
        return ErrorClassification(
            ErrorKind.BILLING,
            fallback_eligible=True,
            status_code=status,
            reason="provider account or quota is unavailable",
            provider=provider,
        )

    if status == 429 or any(
        token in text
        for token in ("rate limit", "rate_limit", "too many requests", "http 429")
    ):
        return ErrorClassification(
            ErrorKind.RATE_LIMIT,
            retryable=True,
            fallback_eligible=True,
            status_code=status,
            reason="provider rate limit",
            provider=provider,
        )

    transport_error = any(
        isinstance(
            item,
            (
                TimeoutError,
                socket.timeout,
                ConnectionResetError,
                RemoteDisconnected,
                urllib.error.URLError,
            ),
        )
        and not isinstance(item, urllib.error.HTTPError)
        for item in _exception_chain(exc)
    )
    if status in {408, 500, 502, 503, 504} or any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection reset",
            "remotedisconnected",
            "http 408",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
        )
    ) or transport_error:
        return ErrorClassification(
            ErrorKind.NETWORK if status in {408, None} else ErrorKind.SERVER,
            retryable=True,
            fallback_eligible=True,
            status_code=status,
            reason="provider endpoint or network is temporarily unavailable",
            provider=provider,
        )

    if status == 404 or any(
        token in text
        for token in ("model not found", "unknown model", "model unavailable", "does not exist")
    ):
        return ErrorClassification(
            ErrorKind.MODEL_UNAVAILABLE,
            fallback_eligible=True,
            status_code=status,
            reason="requested model is unavailable",
            provider=provider,
        )

    if status in {400, 422} or any(
        token in text
        for token in ("invalid request", "validation error", "malformed request")
    ):
        return ErrorClassification(
            ErrorKind.INVALID_REQUEST,
            status_code=status,
            reason="provider rejected the request shape or parameters",
            provider=provider,
        )

    return ErrorClassification(
        ErrorKind.UNKNOWN,
        status_code=status,
        reason="unclassified provider error",
        provider=provider,
    )
