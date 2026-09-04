"""Small, dependency-free security boundaries for external text and URLs."""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class URLSecurityError(ValueError):
    """Raised when a URL violates the explicit network policy."""


def is_private_address(value: str) -> bool:
    """Return whether an address is non-public and unsafe for SSRF by default."""

    address = ipaddress.ip_address(str(value))
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


@dataclass(frozen=True)
class URLTarget:
    original: str
    normalized: str
    scheme: str
    hostname: str
    port: int | None
    resolved_addresses: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "scheme": self.scheme,
            "hostname": self.hostname,
            "port": self.port,
            "resolved_addresses": list(self.resolved_addresses),
        }


def validate_url(
    value: str,
    *,
    allow_private: bool = False,
    resolve_host: bool = False,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
) -> URLTarget:
    """Validate a URL and optionally validate DNS-resolved addresses.

    DNS resolution is intentionally opt-in.  This keeps configuration and
    Doctor's default path offline while giving an explicit network caller an
    SSRF check over both the original host and resolved addresses.
    """

    original = str(value or "").strip()
    parsed = urlparse(original)
    scheme = parsed.scheme.lower()
    if scheme not in {str(item).lower() for item in allowed_schemes}:
        raise URLSecurityError("URL scheme is not allowed")
    if parsed.username or parsed.password:
        raise URLSecurityError("URL credentials are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLSecurityError("URL port is invalid") from exc
    hostname = str(parsed.hostname or "").strip().lower()
    if not hostname:
        raise URLSecurityError("URL hostname is required")
    addresses: list[str] = []
    try:
        literal_private = is_private_address(hostname)
    except ValueError:
        literal_private = False
    if literal_private and not allow_private:
        raise URLSecurityError("private or local URL target is not allowed")
    if resolve_host:
        try:
            addresses = sorted(
                {
                    str(item[4][0])
                    for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
                }
            )
        except socket.gaierror as exc:
            raise URLSecurityError("URL hostname could not be resolved") from exc
        if not addresses:
            raise URLSecurityError("URL hostname resolved to no addresses")
        if not allow_private and any(is_private_address(item) for item in addresses):
            raise URLSecurityError("URL resolves to a private or local address")
    normalized = parsed._replace(
        scheme=scheme,
        netloc=parsed.netloc.lower(),
        fragment="",
    ).geturl()
    return URLTarget(original, normalized, scheme, hostname, port, tuple(addresses))


@dataclass(frozen=True)
class TrustBoundary:
    """Nonce-delimited marker for text that must not become instructions."""

    source: str
    nonce: str

    @property
    def begin(self) -> str:
        return f"<<<UNTRUSTED_TEXT source={self.source!r} nonce={self.nonce}>>>"

    @property
    def end(self) -> str:
        return f"<<<END_UNTRUSTED_TEXT nonce={self.nonce}>>>"

    def wrap(self, text: str) -> str:
        return f"{self.begin}\n{str(text)}\n{self.end}"

    def metadata(self) -> dict[str, str]:
        return {"source": self.source, "nonce": self.nonce}


def new_trust_boundary(
    source: str = "external", nonce: str | None = None
) -> TrustBoundary:
    """Create a reusable boundary so one stored result keeps one nonce."""

    safe_source = re.sub(
        r"[^A-Za-z0-9_.:-]", "_", str(source or "external").replace("\n", " ")
    )[:80]
    return TrustBoundary(
        source=safe_source or "external",
        nonce=str(nonce or secrets.token_urlsafe(12)),
    )


def mark_untrusted_text(
    text: str, source: str = "external", nonce: str | None = None
) -> str:
    """Wrap external text with a fresh nonce-bearing prompt boundary."""

    return new_trust_boundary(source, nonce).wrap(text)
