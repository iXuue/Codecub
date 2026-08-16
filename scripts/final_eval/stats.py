"""Final Evaluation statistics — paired point estimates + bootstrap CIs.

Paired unit = task_id × repeat_index (spec §22). Memory delta = FULL -
CONTEXT_ONLY; Context delta = CONTEXT_ONLY - LEGACY_CONTEXT. Bootstrap:
10,000 resamples, fixed seed, 95% percentile CIs. McNemar when discordant
pairs are sufficient; otherwise explicitly "underpowered / inconclusive".
"""

from __future__ import annotations

import csv
import json
import random
from statistics import median
from pathlib import Path

BOOTSTRAP_SEED = 20260816
BOOTSTRAP_N = 10_000


def _num(value):
    return value if isinstance(value, (int, float)) and value is not None else None


def paired_rows(rows, variant_a, variant_b):
    """Return list of (a_row, b_row) per task_id x repeat, sorted."""
    by_unit = {}
    for row in rows:
        if row.get("run_kind") != "main":
            continue
        key = (row.get("task_id"), row.get("repeat"))
        by_unit.setdefault(key, {})[row.get("variant")] = row
    pairs = []
    for key in sorted(by_unit):
        unit = by_unit[key]
        if variant_a in unit and variant_b in unit:
            pairs.append((unit[variant_a], unit[variant_b]))
    return pairs


def deltas(pairs, field):
    out = []
    for a, b in pairs:
        va, vb = _num(a.get(field)), _num(b.get(field))
        if va is None or vb is None:
            continue
        out.append(va - vb)
    return out


def bootstrap_ci(values, seed=BOOTSTRAP_SEED, n=BOOTSTRAP_N):
    if not values:
        return (None, None)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[int(0.975 * n)]
    return (round(lo, 4), round(hi, 4))


def paired_stats(pairs, field):
    values = deltas(pairs, field)
    if not values:
        return {"field": field, "n": 0, "mean_delta": None, "median_delta": None,
                "ci95": [None, None], "inconclusive": True}
    ci = bootstrap_ci(values)
    inconclusive = ci[0] is not None and ci[0] <= 0 <= ci[1]
    return {
        "field": field,
        "n": len(values),
        "mean_delta": round(sum(values) / len(values), 4),
        "median_delta": round(median(values), 4),
        "ci95": ci,
        "inconclusive": bool(inconclusive),
    }


def mcnemar(pairs):
    """Pass-change discordant counts: (a: both pass, b: A pass B fail,
    c: A fail B pass, d: both fail)."""
    b = c = 0
    for a_row, b_row in pairs:
        pa = bool(a_row.get("passed"))
        pb = bool(b_row.get("passed"))
        if pa and not pb:
            b += 1
        elif pb and not pa:
            c += 1
    total = b + c
    if total == 0:
        return {"b": b, "c": c, "total": 0, "p_value": None, "note": "no discordant pairs"}
    if total < 10:
        return {"b": b, "c": c, "total": total, "p_value": None,
                "note": "underpowered (too few discordant pairs)"}
    # exact binomial two-sided on min(b, c).
    from math import comb

    k = min(b, c)
    p_value = 0.0
    for i in range(k + 1):
        p_value += comb(total, i) * (0.5 ** total)
    p_value = min(1.0, 2 * p_value)
    return {"b": b, "c": c, "total": total, "p_value": round(p_value, 4), "note": ""}


def summary_block(rows, variant):
    subset = [r for r in rows if r.get("variant") == variant and r.get("run_kind") == "main"]
    n = len(subset)
    if not n:
        return {"variant": variant, "n": 0}
    return {
        "variant": variant,
        "n": n,
        "pass_count": sum(bool(r.get("passed")) for r in subset),
        "pass_rate": round(sum(bool(r.get("passed")) for r in subset) / n, 4),
        "verifier_pass_rate": round(sum(bool(r.get("verifier_passed")) for r in subset) / n, 4),
        "mean_tool_steps": round(sum(r.get("tool_steps") or 0 for r in subset) / n, 2),
        "median_tool_steps": median([r.get("tool_steps") or 0 for r in subset]),
        "mean_attempts": round(sum(r.get("attempts") or 0 for r in subset) / n, 2),
        "mean_input_tokens": round(
            sum(r.get("input_tokens") or 0 for r in subset) / n, 1
        ) if any(r.get("input_tokens") for r in subset) else None,
        "mean_output_tokens": round(
            sum(r.get("output_tokens") or 0 for r in subset) / n, 1
        ) if any(r.get("output_tokens") for r in subset) else None,
        "mean_duration_ms": round(sum(r.get("duration_ms") or 0 for r in subset) / n, 1),
        "mean_repeated_reads": round(
            sum(r.get("repeated_read_calls") or 0 for r in subset) / n, 3
        ),
        "mean_first_relevant_source": round(
            sum(r.get("first_relevant_source_step") or 0 for r in subset) / n, 2
        ),
        "mean_search_before_relevant": round(
            sum(r.get("search_calls_before_relevant_source") or 0 for r in subset) / n, 2
        ),
        "workspace_change_rate": round(
            sum(bool(r.get("workspace_changed")) for r in subset) / n, 4
        ),
        "verification_after_change_rate": round(
            sum(bool(r.get("verification_after_change")) for r in subset) / n, 4
        ),
        "recovery_count": sum(r.get("recovery_turn_count") or 0 for r in subset),
        "stuck_confirmed_count": sum(r.get("stuck_confirmed_count") or 0 for r in subset),
        "compression_count": sum(r.get("compression_count") or 0 for r in subset),
        "mean_context_reduction_ratio": round(
            sum(r.get("context_reduction_ratio") or 0 for r in subset) / n, 4
        ),
        "mean_compiled_model_visible_tokens": round(
            sum(r.get("compiled_model_visible_tokens") or 0 for r in subset) / n, 1
        ),
        "mean_raw_model_visible_tokens": round(
            sum(r.get("raw_model_visible_tokens") or 0 for r in subset) / n, 1
        ),
        "mean_memory_injected_tokens": round(
            sum(r.get("memory_injected_tokens") or 0 for r in subset) / n, 1
        ),
        "memory_hit_count": sum(bool(r.get("memory_hit")) for r in subset),
        "mean_memory_stale_used_without_revalidation": round(
            sum(r.get("memory_stale_used_without_revalidation") or 0 for r in subset) / n, 4
        ),
        "stop_reasons": _counts([r.get("stop_reason") for r in subset]),
    }


def _counts(items):
    counts = {}
    for item in items:
        counts[str(item)] = counts.get(str(item), 0) + 1
    return counts


def build_statistics(rows):
    context_pairs = paired_rows(rows, "V_CONTEXT_ONLY", "V_LEGACY_CONTEXT")
    memory_pairs = paired_rows(rows, "V_FULL", "V_CONTEXT_ONLY")
    stress_rows = [r for r in rows if r.get("run_kind") == "stress"]
    normal_full = {
        (r.get("task_id"), r.get("repeat")): r
        for r in rows
        if r.get("run_kind") == "main" and r.get("variant") == "V_FULL"
    }
    stress_pairs = []
    for s in stress_rows:
        control = normal_full.get((s.get("task_id"), s.get("repeat")))
        if control is not None:
            stress_pairs.append((control, s))

    stats = {
        "generation": "codecub-v2-final-g1",
        "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED},
        "variants": {
            v: summary_block(rows, v) for v in ("V_FULL", "V_CONTEXT_ONLY", "V_LEGACY_CONTEXT")
        },
        "context_ablation": {
            "pairs": len(context_pairs),
            "pass_delta": paired_stats(context_pairs, "passed"),
            "tool_step_delta": paired_stats(context_pairs, "tool_steps"),
            "token_delta": paired_stats(context_pairs, "input_tokens"),
            "compiled_token_delta": paired_stats(context_pairs, "compiled_model_visible_tokens"),
            "repeated_read_delta": paired_stats(context_pairs, "repeated_read_calls"),
            "mcnemar": mcnemar(context_pairs),
            "paired_data": [
                {
                    "task_id": a.get("task_id"),
                    "repeat": a.get("repeat"),
                    "context_passed": bool(a.get("passed")),
                    "legacy_passed": bool(b.get("passed")),
                    "context_steps": a.get("tool_steps"),
                    "legacy_steps": b.get("tool_steps"),
                    "context_input_tokens": a.get("input_tokens"),
                    "legacy_input_tokens": b.get("input_tokens"),
                }
                for a, b in context_pairs
            ],
        },
        "memory_ablation": {
            "pairs": len(memory_pairs),
            "pass_delta": paired_stats(memory_pairs, "passed"),
            "tool_step_delta": paired_stats(memory_pairs, "tool_steps"),
            "repeated_read_delta": paired_stats(memory_pairs, "repeated_read_calls"),
            "search_before_delta": paired_stats(
                memory_pairs, "search_calls_before_relevant_source"
            ),
            "first_relevant_source_delta": paired_stats(
                memory_pairs, "first_relevant_source_step"
            ),
            "input_token_delta": paired_stats(memory_pairs, "input_tokens"),
            "mcnemar": mcnemar(memory_pairs),
            "paired_data": [
                {
                    "task_id": a.get("task_id"),
                    "repeat": a.get("repeat"),
                    "full_passed": bool(a.get("passed")),
                    "context_passed": bool(b.get("passed")),
                    "full_steps": a.get("tool_steps"),
                    "context_steps": b.get("tool_steps"),
                    "full_repeated_reads": a.get("repeated_read_calls"),
                    "context_repeated_reads": b.get("repeated_read_calls"),
                    "full_memory_tokens": a.get("memory_injected_tokens"),
                }
                for a, b in memory_pairs
            ],
        },
        "long_horizon": _long_horizon_stats(rows),
        "stress": {
            "runs": len(stress_rows),
            "passed": sum(bool(r.get("passed")) for r in stress_rows),
            "paired_with_normal": len(stress_pairs),
            "pass_delta_normal_minus_fault": _stress_pass_delta(stress_pairs),
            "tool_overhead_mean": _stress_overhead(stress_pairs, "tool_steps"),
            "token_overhead_mean": _stress_overhead(stress_pairs, "input_tokens"),
            "duration_overhead_mean": _stress_overhead(stress_pairs, "duration_ms"),
            "recovery_turn_count": sum(r.get("recovery_turn_count") or 0 for r in stress_rows),
            "stuck_confirmed_count": sum(r.get("stuck_confirmed_count") or 0 for r in stress_rows),
            "rows": [
                {
                    "task_id": r.get("task_id"),
                    "repeat": r.get("repeat"),
                    "fault": r.get("fault"),
                    "passed": bool(r.get("passed")),
                    "verifier_passed": bool(r.get("verifier_passed")),
                    "tool_steps": r.get("tool_steps"),
                    "stop_reason": r.get("stop_reason"),
                }
                for r in stress_rows
            ],
        },
    }
    return stats


def _long_horizon_stats(rows):
    lh = [r for r in rows if r.get("long_horizon") and r.get("run_kind") == "main"]
    if not lh:
        return {}
    passed = [r for r in lh if r.get("passed")]
    return {
        "runs": len(lh),
        "pass_count": len(passed),
        "runs_gt_24": sum((r.get("tool_steps") or 0) > 24 for r in lh),
        "runs_gt_40": sum((r.get("tool_steps") or 0) > 40 for r in lh),
        "passed_gt_24": sum((r.get("tool_steps") or 0) > 24 for r in passed),
        "passed_gt_40": sum((r.get("tool_steps") or 0) > 40 for r in passed),
        "passed_after_compression": sum(
            (r.get("compression_count") or 0) >= 1 for r in passed
        ),
        "passed_after_verification_failure": sum(
            (r.get("verification_steps") or 0) >= 2 and (r.get("verification_after_change") or 0) > 0
            for r in passed
        ),
        "passed_after_recovery": sum((r.get("recovery_turn_count") or 0) >= 1 for r in passed),
        "mean_edit_decisions": round(
            sum(r.get("edit_decision_count") or 0 for r in lh) / len(lh), 2
        ),
        "mean_compression": round(
            sum(r.get("compression_count") or 0 for r in lh) / len(lh), 2
        ),
        "mean_context_reduction_ratio": round(
            sum(r.get("context_reduction_ratio") or 0 for r in lh) / len(lh), 4
        ),
        "memory_hit_count": sum(bool(r.get("memory_hit")) for r in lh),
    }


def _stress_pass_delta(pairs):
    deltas = [bool(a.get("passed")) - bool(b.get("passed")) for a, b in pairs]
    return round(sum(deltas) / len(deltas), 4) if deltas else None


def _stress_overhead(pairs, field):
    values = []
    for normal, fault in pairs:
        nv, fv = _num(normal.get(field)), _num(fault.get(field))
        if nv is None or fv is None or nv == 0:
            continue
        values.append((fv - nv) / nv)
    return round(sum(values) / len(values), 4) if values else None


def write_machine_outputs(output_root, rows, stats):
    output_root = Path(output_root)
    metrics_dir = output_root / "metrics"
    statistics_dir = output_root / "statistics"
    reports_dir = output_root / "reports"
    for path in (metrics_dir, statistics_dir, reports_dir):
        path.mkdir(parents=True, exist_ok=True)

    main_rows = [r for r in rows if r.get("run_kind") == "main"]
    stress_rows = [r for r in rows if r.get("run_kind") == "stress"]
    fields = [
        "run_id", "task_id", "long_horizon", "variant", "repeat", "run_kind",
        "passed", "verifier_passed", "within_budget", "workspace_changed",
        "status", "stop_reason", "tool_steps", "attempts", "duration_ms",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "workspace_change_count", "verification_steps",
        "first_relevant_source_step", "search_calls_before_relevant_source",
        "repeated_read_calls", "avoidable_repeated_read_calls",
        "memory_hit", "memory_injected_tokens", "memory_retrieval_count",
        "memory_stale_retrieval_count", "memory_revalidated_count",
        "memory_stale_used_without_revalidation", "memory_guided_reread_count",
        "compression_count", "raw_model_visible_tokens",
        "compiled_model_visible_tokens", "context_reduction_ratio",
        "provider_actual_input_tokens", "hysteresis_thrashing",
        "stuck_suspected_count", "recovery_turn_count", "recovery_success_count",
        "stuck_confirmed_count", "edit_decision_count",
        "edit_decision_evidence_rejected", "fault", "provider_retry_count",
    ]

    def write_csv(name, rows_subset):
        with (metrics_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows_subset:
                writer.writerow({k: row.get(k) for k in fields})

    write_csv("final_runs.csv", main_rows)
    write_csv("stress_results.csv", stress_rows)

    with (metrics_dir / "paired_context.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["task_id", "repeat", "context_passed", "legacy_passed",
                        "context_steps", "legacy_steps", "context_input_tokens",
                        "legacy_input_tokens"],
        )
        writer.writeheader()
        writer.writerows(stats["context_ablation"]["paired_data"])
    with (metrics_dir / "paired_memory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["task_id", "repeat", "full_passed", "context_passed",
                        "full_steps", "context_steps", "full_repeated_reads",
                        "context_repeated_reads", "full_memory_tokens"],
        )
        writer.writeheader()
        writer.writerows(stats["memory_ablation"]["paired_data"])

    summary = {
        "generation": stats["generation"],
        "variants": {
            v: {
                k: block[k]
                for k in ("n", "pass_count", "pass_rate", "verifier_pass_rate",
                          "mean_tool_steps", "median_tool_steps", "mean_input_tokens",
                          "mean_output_tokens", "mean_duration_ms", "mean_repeated_reads",
                          "mean_first_relevant_source", "workspace_change_rate",
                          "recovery_count", "stuck_confirmed_count", "compression_count",
                          "mean_context_reduction_ratio", "mean_memory_injected_tokens",
                          "memory_hit_count", "stop_reasons")
            }
            for v, block in stats["variants"].items()
        },
        "context_ablation": {
            k: stats["context_ablation"][k]
            for k in ("pairs", "pass_delta", "tool_step_delta", "token_delta",
                      "compiled_token_delta", "repeated_read_delta", "mcnemar")
        },
        "memory_ablation": {
            k: stats["memory_ablation"][k]
            for k in ("pairs", "pass_delta", "tool_step_delta", "repeated_read_delta",
                      "search_before_delta", "first_relevant_source_delta",
                      "input_token_delta", "mcnemar")
        },
        "long_horizon": stats["long_horizon"],
        "stress": {k: v for k, v in stats["stress"].items() if k != "rows"},
    }
    (statistics_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (statistics_dir / "statistics.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
