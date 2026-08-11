from .rightcode import parse_rightcode_claude_usage, parse_rightcode_codex_usage


def parse_openai_compatible_usage(payload, connection_profile, model="", endpoint_kind="responses"):
    record = parse_rightcode_codex_usage(payload, connection_profile, model=model, endpoint_kind=endpoint_kind)
    prefix = connection_profile.id.replace("-official", "").replace("-", "_")
    record["calculation_channels"] = {
        "context": f"{prefix}_{endpoint_kind}_usage",
        "cache": _cache_channel(connection_profile.id, endpoint_kind),
        "cost": f"{prefix}_official_pricing_unavailable",
    }
    if connection_profile.id == "openai-official":
        usage = record.get("provider_native_metrics", {})
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        input_tokens = record["context"].get("actual_input_tokens")
        record["cache"] = {
            "read_tokens": cached if isinstance(cached, int) else None,
            "write_tokens": None,
            "uncached_input_tokens": max(0, input_tokens - cached) if isinstance(input_tokens, int) and isinstance(cached, int) else None,
            "mode": "provider_managed",
            "ratio_definition": "cached_input_tokens / input_tokens" if isinstance(cached, int) else None,
            "comparability": "provider_specific",
        }
        record["warnings"] = [warning for warning in record["warnings"] if not warning.startswith("rightcode_")]
    elif connection_profile.id == "deepseek-official" and endpoint_kind == "responses":
        usage = record.get("provider_native_metrics", {})
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        input_tokens = record["context"].get("actual_input_tokens")
        record["cache"] = {
            "read_tokens": cached if isinstance(cached, int) else None,
            "write_tokens": None,
            "uncached_input_tokens": max(0, input_tokens - cached) if isinstance(input_tokens, int) and isinstance(cached, int) else None,
            "mode": "provider_managed",
            "ratio_definition": "cached_input_tokens / input_tokens" if isinstance(cached, int) else None,
            "comparability": "provider_specific",
        }
        record["calculation_channels"] = {
            "context": "deepseek_responses_usage",
            "cache": "deepseek_responses_cache_usage",
            "cost": "deepseek_official_pricing_unavailable",
        }
        record["warnings"] = [warning for warning in record["warnings"] if not warning.startswith("rightcode_")]
        if cached is None:
            record["warnings"].append("deepseek_responses_cache_fields_missing")
    elif connection_profile.id == "deepseek-official":
        usage = record.get("provider_native_metrics", {})
        prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        hit = usage.get("prompt_cache_hit_tokens") if isinstance(usage, dict) else None
        miss = usage.get("prompt_cache_miss_tokens") if isinstance(usage, dict) else None
        if all(isinstance(value, int) and value >= 0 for value in (prompt, hit, miss)) and prompt == hit + miss:
            record["context"]["actual_input_tokens"] = prompt
            record["cache"] = {"read_tokens": hit, "write_tokens": None, "uncached_input_tokens": miss, "mode": "provider_managed", "ratio_definition": "prompt_cache_hit_tokens / prompt_tokens", "comparability": "provider_specific"}
            record["calculation_channels"] = {"context": "deepseek_official_chat_usage", "cache": "deepseek_official_cache_usage", "cost": "deepseek_official_pricing_unavailable"}
        else:
            record["context"]["actual_input_tokens"] = None
            record["warnings"].append("deepseek_prompt_token_mismatch_or_missing")
    else:
        input_tokens = record["context"].get("actual_input_tokens")
        record["cache"] = {
            "read_tokens": None,
            "write_tokens": None,
            "uncached_input_tokens": input_tokens,
            "mode": "unverified",
            "ratio_definition": None,
            "comparability": "not_comparable",
        }
        record["warnings"] = [warning for warning in record["warnings"] if not warning.startswith("rightcode_")]
        record["warnings"].append(f"{prefix}_cache_fields_unverified")
    return record


def parse_anthropic_usage(payload, connection_profile, model=""):
    record = parse_rightcode_claude_usage(payload, connection_profile, model=model)
    if connection_profile.id != "anthropic-official":
        return record
    prefix = "anthropic"
    record["calculation_channels"] = {
        "context": f"{prefix}_messages_usage",
        "cache": f"{prefix}_cache_usage",
        "cost": f"{prefix}_pricing_unavailable",
    }
    record["warnings"] = [warning for warning in record["warnings"] if not warning.startswith("rightcode_")]
    return record


def _cache_channel(profile_id, endpoint_kind):
    if profile_id == "openai-official":
        return f"openai_{endpoint_kind}_cache"
    return "unverified"
