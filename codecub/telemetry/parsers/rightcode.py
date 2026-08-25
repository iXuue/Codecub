def _usage_object(payload):
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    return dict(usage) if isinstance(usage, dict) else dict(payload)


def _optional_nonnegative_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _nested_int(mapping, *path):
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _optional_nonnegative_int(current)


def _base_record(connection_profile, raw_usage, model, protocol):
    return {
        "schema_version": 2,
        "connection_profile_id": connection_profile.id,
        "connection_type": connection_profile.connection_type,
        "api_operator": connection_profile.api_operator,
        "model_vendor": connection_profile.model_vendor,
        "protocol": protocol,
        "endpoint_kind": protocol,
        "response_schema": connection_profile.response_schema,
        "endpoint_verification_status": connection_profile.endpoint_verification_status,
        "usage_schema_verification_status": connection_profile.usage_schema_verification_status,
        "model": str(model or ""),
        "calculation_channels": {
            "context": "unassigned",
            "cache": "unassigned",
            "cost": "rightcode_response_or_statement",
        },
        "context": {},
        "cache": {},
        "output": {},
        "cost": {
            "upstream_reference_cost": None,
            "operator_metered_cost": None,
            "operator_billed_cost": None,
            "cash_equivalent_cost": None,
            "quality": "unavailable",
        },
        "provider_native_metrics": {},
        "raw_usage": raw_usage,
        "warnings": [],
        "provenance": {
            "source": "relay_response",
            "quality": "unverified",
        },
    }


def parse_rightcode_codex_usage(payload, connection_profile, model="", endpoint_kind="responses"):
    usage = _usage_object(payload)
    input_tokens = _optional_nonnegative_int(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _optional_nonnegative_int(usage.get("output_tokens", usage.get("completion_tokens")))
    total_tokens = _optional_nonnegative_int(usage.get("total_tokens"))
    cached_tokens = _nested_int(usage, "input_tokens_details", "cached_tokens")
    if cached_tokens is None:
        cached_tokens = _nested_int(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _nested_int(usage, "output_tokens_details", "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _nested_int(usage, "completion_tokens_details", "reasoning_tokens")
    context_window = _optional_nonnegative_int(payload.get("context_window")) if isinstance(payload, dict) else None

    record = _base_record(connection_profile, usage, model, endpoint_kind)
    record["calculation_channels"]["context"] = (
        "rightcode_codex_chat_usage" if endpoint_kind == "chat_completions" else "rightcode_codex_responses_usage"
    )
    record["calculation_channels"]["cache"] = (
        "unsupported" if endpoint_kind == "chat_completions" else "rightcode_codex_responses_cache"
    )
    record["context"] = {
        "actual_input_tokens": input_tokens,
        "count_source": "relay_response" if input_tokens is not None else "unavailable",
        "count_quality": "operator_reported" if input_tokens is not None else "unavailable",
        "context_window": context_window,
        "window_source": "relay_response" if context_window else "unavailable",
    }
    if endpoint_kind == "chat_completions":
        record["cache"] = {
            "read_tokens": None,
            "write_tokens": None,
            "uncached_input_tokens": input_tokens,
            "mode": "unavailable",
            "ratio_definition": None,
            "comparability": "not_comparable",
        }
        record["warnings"].append("rightcode_chat_cache_schema_unverified")
    else:
        uncached = None
        if input_tokens is not None and cached_tokens is not None:
            uncached = max(0, input_tokens - cached_tokens)
        record["cache"] = {
            "read_tokens": cached_tokens,
            "write_tokens": None,
            "uncached_input_tokens": uncached,
            "mode": "operator_managed",
            "ratio_definition": "cached_input_tokens / input_tokens" if cached_tokens is not None else None,
            "comparability": "provider_specific",
        }
        if cached_tokens is None:
            record["warnings"].append("rightcode_codex_cache_fields_unverified_or_missing")
    record["output"] = {
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }
    record["provider_native_metrics"] = dict(usage)
    if not usage:
        record["warnings"].append("rightcode_usage_missing")
    return record


def parse_rightcode_claude_usage(payload, connection_profile, model=""):
    usage = _usage_object(payload)
    regular_input = _optional_nonnegative_int(usage.get("input_tokens"))
    output_tokens = _optional_nonnegative_int(usage.get("output_tokens"))
    cache_write = _optional_nonnegative_int(usage.get("cache_creation_input_tokens"))
    cache_read = _optional_nonnegative_int(usage.get("cache_read_input_tokens"))
    cache_write_5m = _nested_int(usage, "cache_creation", "ephemeral_5m_input_tokens")
    cache_write_1h = _nested_int(usage, "cache_creation", "ephemeral_1h_input_tokens")
    context_window = _optional_nonnegative_int(payload.get("context_window")) if isinstance(payload, dict) else None
    actual_input = None
    if regular_input is not None:
        # A relay omitting cache fields is not evidence that they were zero.
        if connection_profile.usage_schema_verification_status == "verified" or (cache_write is not None and cache_read is not None):
            actual_input = regular_input + (cache_write or 0) + (cache_read or 0)

    record = _base_record(connection_profile, usage, model, "anthropic_messages")
    record["calculation_channels"]["context"] = "rightcode_claude_messages_usage"
    record["calculation_channels"]["cache"] = "rightcode_claude_cache_usage"
    record["context"] = {
        "actual_input_tokens": actual_input,
        "count_source": "relay_response" if actual_input is not None else "unavailable",
        "count_quality": "operator_reported" if actual_input is not None else "unavailable",
        "context_window": context_window,
        "window_source": "relay_response" if context_window else "unavailable",
    }
    record["cache"] = {
        "read_tokens": cache_read,
        "write_tokens": cache_write,
        "write_5m_tokens": cache_write_5m,
        "write_1h_tokens": cache_write_1h,
        "write_unknown_ttl_tokens": cache_write if cache_write is not None and cache_write_5m is None and cache_write_1h is None else None,
        "uncached_input_tokens": regular_input,
        "mode": "explicit_ephemeral",
        "ratio_definition": "cache_read_tokens / total_input_tokens" if cache_read is not None else None,
        "comparability": "provider_specific",
    }
    record["output"] = {
        "output_tokens": output_tokens,
        "reasoning_tokens": None,
        "total_tokens": None if actual_input is None or output_tokens is None else actual_input + output_tokens,
    }
    record["provider_native_metrics"] = dict(usage)
    if cache_read is None or cache_write is None:
        record["warnings"].append("rightcode_claude_cache_fields_unverified_or_missing")
    if not usage:
        record["warnings"].append("rightcode_usage_missing")
    return record
