import hashlib
from urllib.parse import urlparse

from .schema import ApiConnectionProfile


RIGHTCODE_CODEX = ApiConnectionProfile(
    id="rightcode-codex",
    display_name="Right Code Codex",
    connection_type="relay",
    api_operator="right.codes",
    endpoint_origin="verified-relay",
    base_url="https://www.right.codes/codex/v1",
    protocol="openai-responses",
    response_schema="rightcode-codex-unverified",
    model_vendor="openai",
    credential_id="rightcode",
    endpoint_verification_status="verified",
    usage_schema_verification_status="unverified",
    prompt_cache_request_mode="provider_managed",
    prompt_cache_request_support_status="unverified",
)

RIGHTCODE_CLAUDE = ApiConnectionProfile(
    id="rightcode-claude",
    display_name="Right Code Claude",
    connection_type="relay",
    api_operator="right.codes",
    endpoint_origin="verified-relay",
    base_url="https://www.right.codes/claude/v1",
    protocol="anthropic-messages",
    response_schema="rightcode-claude-unverified",
    model_vendor="anthropic",
    credential_id="rightcode",
    endpoint_verification_status="verified",
    usage_schema_verification_status="unverified",
    prompt_cache_request_mode="explicit_ephemeral",
    prompt_cache_request_support_status="unverified",
)

OPENAI_OFFICIAL = ApiConnectionProfile(
    "openai-official",
    "OpenAI Official",
    "direct",
    "openai",
    "verified-direct",
    "https://api.openai.com/v1",
    "openai-responses",
    "openai-responses",
    "openai",
    "openai-official",
    "verified",
    "verified",
    "provider_managed",
    "verified",
)
ANTHROPIC_OFFICIAL = ApiConnectionProfile(
    "anthropic-official",
    "Anthropic Official",
    "direct",
    "anthropic",
    "verified-direct",
    "https://api.anthropic.com/v1",
    "anthropic-messages",
    "anthropic-messages",
    "anthropic",
    "anthropic-official",
    "verified",
    "verified",
    "explicit_ephemeral",
    "verified",
)
DEEPSEEK_OFFICIAL = ApiConnectionProfile(
    "deepseek-official",
    "DeepSeek Official",
    "direct",
    "deepseek",
    "verified-direct",
    "https://api.deepseek.com",
    "openai-chat",
    "deepseek-chat",
    "deepseek",
    "deepseek-official",
    "verified",
    "verified",
    "unavailable",
    "unverified",
    True,
    False,
    True,
    False,
)
DEEPSEEK_ANTHROPIC = ApiConnectionProfile(
    "deepseek-anthropic-official",
    "DeepSeek Anthropic API",
    "direct",
    "deepseek",
    "verified-direct",
    "https://api.deepseek.com/anthropic",
    "anthropic-messages",
    "deepseek-anthropic",
    "deepseek",
    "deepseek-official",
    "verified",
    "unverified",
)
KIMI_OFFICIAL = ApiConnectionProfile(
    "kimi-official",
    "Kimi Official",
    "direct",
    "moonshot",
    "verified-direct",
    "https://api.moonshot.cn/v1",
    "openai-chat",
    "moonshot-chat",
    "moonshot",
    "kimi-official",
    "verified",
    "unverified",
)
MINIMAX_OFFICIAL = ApiConnectionProfile(
    "minimax-official",
    "MiniMax Official",
    "direct",
    "minimax",
    "verified-direct",
    "https://api.minimax.io/v1",
    "openai-chat",
    "minimax-chat",
    "minimax",
    "minimax-official",
    "verified",
    "verified",
)


def normalize_connection_url(base_url):
    parsed = urlparse(str(base_url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    path = parsed.path.rstrip("/").lower()
    return parsed.scheme.lower(), parsed.hostname.lower(), path


def resolve_connection_profile(base_url, protocol_hint=""):
    normalized = normalize_connection_url(base_url)
    if normalized is None:
        return None
    _, host, path = normalized
    if (
        host == "api.openai.com"
        and path == "/v1"
        and protocol_hint in {"openai-responses", "openai-chat"}
    ):
        return OPENAI_OFFICIAL
    if (
        host == "api.anthropic.com"
        and path == "/v1"
        and protocol_hint == "anthropic-messages"
    ):
        return ANTHROPIC_OFFICIAL
    if (
        host == "api.deepseek.com"
        and path in {"", "/v1"}
        and protocol_hint == "openai-chat"
    ):
        return DEEPSEEK_OFFICIAL
    if (
        host == "api.deepseek.com"
        and path == "/anthropic"
        and protocol_hint == "anthropic-messages"
    ):
        return DEEPSEEK_ANTHROPIC
    if host == "api.moonshot.cn" and path == "/v1" and protocol_hint == "openai-chat":
        return KIMI_OFFICIAL
    if host == "api.minimax.io" and path == "/v1" and protocol_hint == "openai-chat":
        return MINIMAX_OFFICIAL
    if host not in {"right.codes", "www.right.codes", "rightapi.ai", "www.rightapi.ai"}:
        return None
    if "/codex/" in path or path.endswith("/codex/v1"):
        return RIGHTCODE_CODEX
    if "/claude/" in path or path.endswith("/claude/v1"):
        return RIGHTCODE_CLAUDE
    if protocol_hint == "anthropic-messages":
        return RIGHTCODE_CLAUDE
    if protocol_hint in {"openai-responses", "openai-chat"}:
        return RIGHTCODE_CODEX
    return None


def resolve_effective_connection_profile(
    base_url, protocol_hint="", supplied_profile=None
):
    """Known endpoint identities always win; unknown endpoints are custom and unverified."""
    known = resolve_connection_profile(base_url, protocol_hint)
    if known is not None:
        return known
    normalized = normalize_connection_url(base_url)
    if normalized is None:
        return None
    _, host, path = normalized
    supplied = supplied_profile if isinstance(supplied_profile, dict) else {}
    protocol = str(supplied.get("protocol") or protocol_hint or "openai-chat")
    if protocol not in {
        "openai-responses",
        "openai-chat",
        "anthropic-messages",
        "ollama-generate",
    }:
        protocol = "openai-chat"
    vendor = str(supplied.get("model_vendor") or "custom").strip().lower() or "custom"
    fingerprint = hashlib.sha256(
        f"{host}|{path}|{protocol}|{vendor}".encode("utf-8")
    ).hexdigest()[:12]
    return ApiConnectionProfile(
        id=f"custom-{fingerprint}",
        display_name="Custom compatible API",
        connection_type="custom",
        api_operator=host,
        endpoint_origin="unverified-custom",
        base_url=f"https://{host}{path}",
        protocol=protocol,
        response_schema="custom-unverified",
        model_vendor=vendor,
        credential_id=f"custom:{fingerprint}",
        endpoint_verification_status="unverified",
        usage_schema_verification_status="unverified",
    )
