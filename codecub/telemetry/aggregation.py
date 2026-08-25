def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def aggregate_usage_records(records):
    rows = [dict(record) for record in records if isinstance(record, dict)]

    def total(path):
        values = []
        for row in rows:
            current = row
            for key in path:
                current = current.get(key) if isinstance(current, dict) else None
            value = _integer(current)
            if value is not None:
                values.append(value)
        return sum(values) if values else None

    cache_read = total(("cache", "read_tokens"))
    uncached = total(("cache", "uncached_input_tokens"))
    actual_input = total(("context", "actual_input_tokens"))
    request_inputs = [_integer((row.get("context") or {}).get("actual_input_tokens")) for row in rows]
    request_inputs = [value for value in request_inputs if value is not None]
    ratio = None
    if cache_read is not None and actual_input is not None and actual_input > 0:
        ratio = cache_read / actual_input

    unknown_cost_requests = 0
    for row in rows:
        cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
        known_cost = any(
            cost.get(key) is not None
            for key in (
                "upstream_reference_cost",
                "operator_metered_cost",
                "operator_billed_cost",
                "cash_equivalent_cost",
            )
        )
        if not known_cost:
            unknown_cost_requests += 1

    return {
        "schema_version": 1,
        "request_count": len(rows),
        "actual_input_tokens": actual_input,
        "latest_actual_input_tokens": request_inputs[-1] if request_inputs else None,
        "peak_actual_input_tokens": max(request_inputs) if request_inputs else None,
        "output_tokens": total(("output", "output_tokens")),
        "reasoning_tokens": total(("output", "reasoning_tokens")),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": total(("cache", "write_tokens")),
        "uncached_input_tokens": uncached,
        "weighted_cache_read_ratio": ratio,
        "unknown_cost_request_count": unknown_cost_requests,
    }
