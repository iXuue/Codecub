import hashlib
from decimal import Decimal, InvalidOperation

from .aggregation import aggregate_usage_records


PRIVATE_USAGE_FIELDS = {"raw_usage", "provider_native_metrics"}


def safe_usage_record(record):
    if not isinstance(record, dict):
        return {}
    return {key: value for key, value in record.items() if key not in PRIVATE_USAGE_FIELDS}


def usage_group_key(record):
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    parts = [
        record.get("connection_profile_id", "unknown"),
        record.get("api_operator", "unknown"),
        record.get("model_vendor", "unknown"),
        record.get("protocol", "unknown"),
        record.get("endpoint_kind", record.get("protocol", "unknown")),
        record.get("response_schema", "unknown"),
        record.get("model", "unknown"),
        record.get("endpoint_verification_status", record.get("verification_status", "unverified")),
        record.get("usage_schema_verification_status", "unverified"),
        cost_signature(record),
        context.get("context_window", "unknown"),
    ]
    source = "|".join(str(part or "unknown") for part in parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def build_usage_snapshot(records, scope, session_id="", run_id=""):
    safe_records = [safe_usage_record(record) for record in records if isinstance(record, dict)]
    grouped = {}
    for record in safe_records:
        grouped.setdefault(usage_group_key(record), []).append(record)

    groups = []
    for key, rows in grouped.items():
        first = rows[0]
        summary = aggregate_usage_records(rows)
        context = first.get("context") if isinstance(first.get("context"), dict) else {}
        cache = first.get("cache") if isinstance(first.get("cache"), dict) else {}
        context_window = context.get("context_window")
        latest_input = summary.get("latest_actual_input_tokens")
        peak_input = summary.get("peak_actual_input_tokens")
        latest_utilization = latest_input / context_window if isinstance(context_window, int) and context_window > 0 and isinstance(latest_input, int) else None
        peak_utilization = peak_input / context_window if isinstance(context_window, int) and context_window > 0 and isinstance(peak_input, int) else None
        groups.append(
            {
                "aggregation_key": key,
                "connection": {
                    "connection_profile_id": first.get("connection_profile_id", "unknown"),
                    "connection_type": first.get("connection_type", "unknown"),
                    "api_operator": first.get("api_operator", "unknown"),
                    "model_vendor": first.get("model_vendor", "unknown"),
                    "protocol": first.get("protocol", "unknown"),
                    "endpoint_kind": first.get("endpoint_kind", first.get("protocol", "unknown")),
                    "response_schema": first.get("response_schema", "unknown"),
                    "model": first.get("model", ""),
                    "endpoint_verification_status": first.get("endpoint_verification_status", first.get("verification_status", "unverified")),
                    "usage_schema_verification_status": first.get("usage_schema_verification_status", "unverified"),
                },
                "calculation_channels": dict(first.get("calculation_channels") or {}),
                "context": {
                    "total_actual_input_tokens": summary.get("actual_input_tokens"),
                    "latest_actual_input_tokens": latest_input,
                    "peak_actual_input_tokens": peak_input,
                    "context_window": context_window,
                    "latest_utilization_ratio": latest_utilization,
                    "peak_utilization_ratio": peak_utilization,
                    "count_source": context.get("count_source", "unavailable"),
                    "count_quality": context.get("count_quality", "unavailable"),
                    "window_source": context.get("window_source", "unavailable"),
                },
                "cache": {
                    "read_tokens": summary.get("cache_read_tokens"),
                    "write_tokens": summary.get("cache_write_tokens"),
                    "uncached_input_tokens": summary.get("uncached_input_tokens"),
                    "read_ratio": summary.get("weighted_cache_read_ratio"),
                    "mode": cache.get("mode", "unavailable"),
                    "ratio_definition": cache.get("ratio_definition"),
                    "comparability": cache.get("comparability", "not_comparable"),
                },
                "output": {
                    "output_tokens": summary.get("output_tokens"),
                    "reasoning_tokens": summary.get("reasoning_tokens"),
                },
                "cost": _cost_rows(rows),
                "request_count": len(rows),
                "warnings": sorted({warning for row in rows for warning in row.get("warnings", [])}),
            }
        )

    return {
        "schema_version": 2,
        "scope": scope,
        "revision": len({str(record.get("usage_id")) for record in safe_records if record.get("usage_id")}),
        "last_usage_id": next((record.get("usage_id") for record in reversed(safe_records) if record.get("usage_id")), ""),
        "session_id": session_id,
        "run_id": run_id,
        "groups": groups,
    }


def cost_signature(record):
    cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
    signature = []
    for field, kind in _COST_FIELDS:
        value = cost.get(field)
        if isinstance(value, dict) and value.get("amount") is not None:
            signature.append((kind, value.get("unit"), value.get("unit_kind"), value.get("source"), value.get("pricing_version"), value.get("quality")))
    return tuple(sorted(signature)) or (("no_cost",),)


_COST_FIELDS = (
    ("upstream_reference_cost", "upstream_reference"),
    ("operator_metered_cost", "operator_metered"),
    ("operator_billed_cost", "operator_billed"),
    ("cash_equivalent_cost", "cash_equivalent"),
)


def _cost_rows(records):
    rows = {}
    for record in records:
        cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
        for field, kind in _COST_FIELDS:
            value = cost.get(field)
            if not isinstance(value, dict) or value.get("amount") is None:
                continue
            try:
                amount = Decimal(str(value["amount"]))
            except (InvalidOperation, ValueError):
                continue
            if not amount.is_finite() or amount < 0:
                continue
            key = (kind, value.get("unit", "unknown"), value.get("unit_kind", "unknown"), value.get("source", "unknown"), value.get("pricing_version", "unknown"), value.get("quality", "unknown"))
            rows[key] = rows.get(key, Decimal("0")) + amount
    return [
        {"kind": kind, "amount": format(amount, "f"), "unit": unit, "unit_kind": unit_kind, "source": source, "pricing_version": pricing_version, "quality": quality}
        for (kind, unit, unit_kind, source, pricing_version, quality), amount in sorted(rows.items())
    ]
