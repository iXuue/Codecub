"""Small, dependency-free identity and authorization contracts.

Authentication answers who is connected.  ``CapabilityPolicy`` answers what
that identity may do at the ToolExecutor boundary.  They are deliberately
separate so transport authentication cannot be mistaken for tool permission.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .tooling.contracts import ToolCapability


class AuthError(ValueError):
    """Authentication failed or credentials are malformed."""


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    auth_method: str = "local"
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def has_scope(self, required: str) -> bool:
        required = str(required or "").strip()
        if not required:
            return False
        for granted in self.scopes:
            if granted in {"*", required}:
                return True
            if granted.endswith(":*") and required.startswith(granted[:-1]):
                return True
        return False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "scopes": sorted(self.scopes),
            "auth_method": self.auth_method,
        }


class AuthProvider(Protocol):
    def authenticate(self, credential: str) -> Identity | None: ...


class StaticTokenAuthProvider:
    """Authenticate one operator token without storing it in response payloads."""

    def __init__(self, token: str, identity: Identity | None = None):
        token = str(token or "")
        if not token:
            raise ValueError("static auth token must not be empty")
        self._token = token
        self.identity = identity or Identity(
            "gateway-operator",
            frozenset({"run:*", "tool:*"}),
            auth_method="static_token",
        )

    def authenticate(self, credential: str) -> Identity | None:
        return self.identity if hmac.compare_digest(str(credential or ""), self._token) else None


class FakeAuthProvider:
    """Deterministic provider for local integration tests."""

    def __init__(self, credentials: Mapping[str, Identity]):
        self._credentials = dict(credentials)

    def authenticate(self, credential: str) -> Identity | None:
        return self._credentials.get(str(credential or ""))


@dataclass(frozen=True, slots=True)
class SignedAuthToken:
    subject: str
    scopes: tuple[str, ...] = ()
    issued_at: int = field(default_factory=lambda: int(time.time()))
    expires_at: int | None = None
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "scopes": list(self.scopes),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "claims": dict(self.claims),
        }


class HmacAuthProvider:
    """Issue and verify compact signed tokens for a local deployment boundary."""

    def __init__(self, secret: str, *, auth_method: str = "hmac"):
        if not str(secret or ""):
            raise ValueError("HMAC auth secret must not be empty")
        self._secret = str(secret).encode("utf-8")
        self._auth_method = str(auth_method or "hmac")

    def issue(self, token: SignedAuthToken) -> str:
        payload = _encode(token.to_payload())
        signature = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
        return f"v1.{payload}.{_b64(signature)}"

    def authenticate(self, credential: str) -> Identity | None:
        parts = str(credential or "").split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        payload, supplied = parts[1], parts[2]
        expected = hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected), supplied):
            return None
        try:
            raw = json.loads(_decode(payload).decode("utf-8"))
            token = SignedAuthToken(
                subject=str(raw["subject"]),
                scopes=tuple(str(item) for item in raw.get("scopes", ()) or ()),
                issued_at=int(raw.get("issued_at", 0)),
                expires_at=(int(raw["expires_at"]) if raw.get("expires_at") is not None else None),
                claims=dict(raw.get("claims") or {}),
            )
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return None
        if not token.subject or (token.expires_at is not None and int(time.time()) >= token.expires_at):
            return None
        return Identity(
            token.subject,
            frozenset(token.scopes),
            auth_method=self._auth_method,
            claims=token.claims,
        )


class AuthMiddleware:
    """Normalize a bearer header and delegate verification to an AuthProvider."""

    def __init__(self, provider: AuthProvider):
        self.provider = provider

    def authenticate(self, authorization: str) -> Identity:
        scheme, separator, credential = str(authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not credential.strip():
            raise AuthError("authorization must be a bearer credential")
        identity = self.provider.authenticate(credential.strip())
        if identity is None:
            raise AuthError("credentials were rejected")
        return identity


@dataclass(frozen=True, slots=True)
class ManagedPolicy:
    """Operator-locked configuration fields; enforcement is explicit in callers."""

    locked_fields: frozenset[str] = field(default_factory=frozenset)
    description: str = ""

    def is_locked(self, field_path: str) -> bool:
        return str(field_path or "") in self.locked_fields


class CapabilityPolicy:
    """Runtime authorization gate for tool effects and names."""

    def __init__(self, *, default_deny: bool = False, identity: Identity | None = None):
        self.default_deny = bool(default_deny)
        self._identity = identity

    @property
    def identity(self) -> Identity | None:
        return self._identity

    def bind(self, identity: Identity | None) -> None:
        self._identity = identity

    def allow(self, name: str, tool: Mapping[str, Any] | None = None, _invocation: Any = None) -> bool:
        identity = self._identity
        if identity is None:
            return not self.default_deny
        capability = tool.get("capability") if isinstance(tool, Mapping) else None
        if not isinstance(capability, ToolCapability):
            capability = ToolCapability.from_legacy(tool, name=str(name))
        return any(
            identity.has_scope(required)
            for required in (
                f"tool:{str(name).strip()}",
                f"tool:{capability.effect.value}",
                "tool:*",
            )
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "default_deny": self.default_deny,
            "identity": self._identity.to_public_dict() if self._identity else None,
        }


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _encode(value: Mapping[str, Any]) -> str:
    return _b64(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


__all__ = [
    "AuthError",
    "AuthMiddleware",
    "AuthProvider",
    "CapabilityPolicy",
    "FakeAuthProvider",
    "HmacAuthProvider",
    "Identity",
    "ManagedPolicy",
    "SignedAuthToken",
    "StaticTokenAuthProvider",
]
