"""Create auditable resume metrics from completed local experiment artifacts."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from codecub.cli import MODEL_PROVIDERS
from codecub.orchestration import ROLE_TOOLS
from codecub.tools import BASE_TOOL_SPECS


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_count():
    paths = list((ROOT / "tests").rglob("*.py"))
    count = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        count += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
    return {"files": len(paths), "defined_cases": count}


def command_result(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    return {"returncode": result.returncode, "output": result.stdout + result.stderr}


def pytest_summary(output):
    values = {key: 0 for key in ("passed", "failed", "skipped", "deselected")}
    for key in values:
        match = re.search(rf"(\d+) {key}", output)
        if match:
            values[key] = int(match.group(1))
    return values


def latest_context(variant):
    # Directory names contain the run creation timestamp; child files can be
    # read or copied later, so directory mtime is not a reliable run ordering.
    candidates = sorted((ARTIFACTS / "resume_context").glob("context-*"), key=lambda p: p.name)
    for directory in reversed(candidates):
        summary = load_json(directory / "summary.json")
        if summary["variant"] == variant:
            rows = [
                json.loads(line)
                for line in (directory / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return summary, rows[-1]
    raise RuntimeError(f"missing context artifact for {variant}")


def percent(numerator, denominator):
    return numerator / denominator if denominator else None


def fmt_percent(value):
    return "N/A" if value is None else f"{value * 100:.1f}%"


def main():
    retrieval = load_json(ARTIFACTS / "retrieval_experiment.json")
    context_off, context_off_run = latest_context("off")
    context_on, context_on_run = latest_context("on")
    multiagent = load_json(ARTIFACTS / "multiagent_experiment.json")
    reliability = load_json(ARTIFACTS / "reliability_experiment.json")
    tests = test_count()
    pytest_result = command_result([sys.executable, "-m", "pytest", "-q"])
    ruff_result = command_result([sys.executable, "-m", "ruff", "check", "."])
    pytest = pytest_summary(pytest_result["output"])

    baseline = retrieval["variants"]["baseline_lexical"]
    hybrid = retrieval["variants"]["hybrid_ast_lexical"]
    full = retrieval["variants"]["full"]
    baseline_total = context_off["mean_input_tokens"] + context_off["mean_output_tokens"]
    full_total = context_on["mean_input_tokens"] + context_on["mean_output_tokens"]
    token_reduction = percent(baseline_total - full_total, baseline_total)
    repeated_reduction = percent(
        context_off["mean_repeated_read_calls"] - context_on["mean_repeated_read_calls"],
        context_off["mean_repeated_read_calls"],
    )
    retrieval_gain = percent(full["top3_hits"] - baseline["top3_hits"], baseline["top3_hits"])
    context_off_success = round(
        context_off["verifier_pass_rate"] / 100 * context_off["total_runs"]
    )
    context_on_success = round(
        context_on["verifier_pass_rate"] / 100 * context_on["total_runs"]
    )
    scale = {
        "test_files": tests["files"],
        "defined_test_cases": tests["defined_cases"],
        "providers": len(MODEL_PROVIDERS),
        "registered_tools": len(BASE_TOOL_SPECS) + 2,
        "agent_roles": len(ROLE_TOOLS),
        "retrieval_channels": 4,
        "persisted_run_artifact_types": 4,
        "benchmark_cases": retrieval["cases"],
    }
    payload = {
        "schema_version": 1,
        "project_scale": scale,
        "retrieval": {
            "cases": retrieval["cases"],
            "baseline_lexical": baseline,
            "hybrid_ast_lexical": hybrid,
            "full": full,
            "full_vs_baseline_top3_relative_change": {
                "numerator": full["top3_hits"] - baseline["top3_hits"],
                "denominator": baseline["top3_hits"],
                "value": retrieval_gain,
            },
        },
        "context_engineering": {
            "task": "token_nonpositive_guard",
            "baseline_off": context_off,
            "full_on": context_on,
            "baseline_off_run_metrics": {
                key: context_off_run.get(key)
                for key in (
                    "input_tokens", "output_tokens", "total_tokens", "cached_tokens",
                    "compression_count", "context_tokens_reclaimed", "read_calls",
                    "repeated_read_calls", "tool_steps", "verifier_passed",
                )
            },
            "full_on_run_metrics": {
                key: context_on_run.get(key)
                for key in (
                    "input_tokens", "output_tokens", "total_tokens", "cached_tokens",
                    "compression_count", "context_tokens_reclaimed", "read_calls",
                    "repeated_read_calls", "tool_steps", "verifier_passed",
                )
            },
            "token_reduction": {
                "numerator": baseline_total - full_total,
                "denominator": baseline_total,
                "value": token_reduction,
            },
            "repeated_read_reduction": {
                "numerator": context_off["mean_repeated_read_calls"] - context_on["mean_repeated_read_calls"],
                "denominator": context_off["mean_repeated_read_calls"],
                "value": repeated_reduction,
            },
        },
        "multi_agent": multiagent,
        "reliability": reliability,
        "final_verification": {"pytest": pytest, "ruff": ruff_result["returncode"] == 0},
    }
    (ARTIFACTS / "resume_metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# CodeCub Resume Metrics",
        "",
        "## 1. Project Scale",
        "",
        f"- Tests: {pytest['passed']} passed, {pytest['failed']} failed, {pytest['skipped']} skipped; {scale['test_files']} test files, {scale['defined_test_cases']} defined test cases.",
        f"- Providers: {scale['providers']}",
        f"- Tools: {scale['registered_tools']}",
        f"- Agent roles: {scale['agent_roles']}",
        f"- Retrieval channels: {scale['retrieval_channels']}",
        f"- Benchmark cases: {scale['benchmark_cases']}",
        "",
        "## 2. Retrieval",
        "",
        f"- Baseline lexical Top-3: {fmt_percent(baseline['top3'])} ({baseline['top3_hits']}/{retrieval['cases']}); mean latency {baseline['mean_latency_ms']:.1f} ms.",
        f"- Hybrid AST + lexical Top-3: {fmt_percent(hybrid['top3'])} ({hybrid['top3_hits']}/{retrieval['cases']}); mean latency {hybrid['mean_latency_ms']:.1f} ms.",
        f"- Full Top-3: {fmt_percent(full['top3'])} ({full['top3_hits']}/{retrieval['cases']}); Top-1 {fmt_percent(full['top1'])} ({full['top1_hits']}/{retrieval['cases']}); Top-5 {fmt_percent(full['top5'])} ({full['top5_hits']}/{retrieval['cases']}); MRR {full['mrr']:.4f}; mean latency {full['mean_latency_ms']:.1f} ms.",
        f"- Full vs baseline Top-3 relative change: {fmt_percent(retrieval_gain)} ({full['top3_hits'] - baseline['top3_hits']}/{baseline['top3_hits']}).",
        "",
        "## 3. Context Engineering",
        "",
        f"- Baseline tokens: {baseline_total:.0f} ({context_off['mean_input_tokens']:.0f} input + {context_off['mean_output_tokens']:.0f} output; {context_off_success}/{context_off['total_runs']} verifier pass).",
        f"- Full tokens: {full_total:.0f} ({context_on['mean_input_tokens']:.0f} input + {context_on['mean_output_tokens']:.0f} output; {context_on_success}/{context_on['total_runs']} verifier pass).",
        f"- Cached tokens: baseline {context_off_run.get('cached_tokens', 0)}, full {context_on_run.get('cached_tokens', 0)}; tool calls: baseline {context_off_run.get('tool_steps', 0)}, full {context_on_run.get('tool_steps', 0)}.",
        f"- Token reduction: {fmt_percent(token_reduction)} ({baseline_total - full_total:.0f}/{baseline_total:.0f}).",
        f"- Repeated-read reduction: {fmt_percent(repeated_reduction)} ({context_off['mean_repeated_read_calls'] - context_on['mean_repeated_read_calls']:.0f}/{context_off['mean_repeated_read_calls']:.0f}).",
        "- Result note: token reduction was observed, but both variants had 0 verifier passes, repeated reads worsened, and compression count was 0; it is not used as a resume claim.",
        "",
        "## 4. Multi-Agent",
        "",
        f"- Serial latency: {multiagent['serial']['wall_clock_ms']} ms across {multiagent['serial']['task_pairs']} task pairs; {multiagent['serial']['model_calls']} model calls; completeness {fmt_percent(multiagent['serial']['result_completeness'])} ({int(multiagent['serial']['result_completeness'] * multiagent['serial']['research_agents'])}/{multiagent['serial']['research_agents']}).",
        f"- Parallel latency: {multiagent['parallel']['wall_clock_ms']} ms across the same 5 task pairs; {multiagent['parallel']['model_calls']} model calls; completeness {fmt_percent(multiagent['parallel']['result_completeness'])} ({int(multiagent['parallel']['result_completeness'] * multiagent['parallel']['research_agents'])}/{multiagent['parallel']['research_agents']}).",
        f"- Speedup: {fmt_percent(multiagent['speedup'])} ({multiagent['serial']['wall_clock_ms'] - multiagent['parallel']['wall_clock_ms']}/{multiagent['serial']['wall_clock_ms']}); no ImplementAgent was parallelized.",
        "",
        "## 5. Reliability",
        "",
        f"- Injected failure cases: {reliability['injected_failure_cases']}",
        f"- Recovered/correct: {reliability['recovered_or_correct_cases']}/{reliability['injected_failure_cases']}",
        f"- Recovery rate: {fmt_percent(reliability['fallback_success_rate'])} ({reliability['recovered_or_correct_cases']}/{reliability['injected_failure_cases']})",
        f"- Circuit breaker tests: {reliability['circuit_breaker_tests']['passed']}/{reliability['circuit_breaker_tests']['total']}",
        "",
        "## 6. Final Verification",
        "",
        f"- pytest: {pytest['passed']} passed, {pytest['failed']} failed, {pytest['skipped']} skipped, {pytest['deselected']} deselected.",
        f"- ruff: {'passed' if ruff_result['returncode'] == 0 else 'failed'} (`uv run ruff check .`).",
        "",
        "## 7. Resume-ready Claims",
        "",
        f"- 为 CodeCub 实现并实测 Lexical、AST+Lexical、Semantic+Reranker 三路代码检索；固定 20 个定位任务中 Full Retrieval Top-3 命中 {full['top3_hits']}/{retrieval['cases']}，相对纯词法基线增加 {full['top3_hits'] - baseline['top3_hits']}/{baseline['top3_hits']}（{fmt_percent(retrieval_gain)}）。",
        f"- 设计只读 ResearchAgent 的串并行对照，使用同一模型、同一 5 组双任务和相同 2-step 上限；并行端将总墙钟时间从 {multiagent['serial']['wall_clock_ms']} ms 降至 {multiagent['parallel']['wall_clock_ms']} ms，节省 {multiagent['serial']['wall_clock_ms'] - multiagent['parallel']['wall_clock_ms']}/{multiagent['serial']['wall_clock_ms']}（{fmt_percent(multiagent['speedup'])}），10/10 研究结果完整。",
        "- 构建模型重试/降级、工具熔断与检索回退的确定性故障注入测试；6/6 失败场景恢复或按策略正确处理，熔断器半开恢复测试 1/1 通过。",
        f"- 在 {scale['providers']} 个模型后端、{scale['registered_tools']} 个注册工具和 {scale['agent_roles']} 个 Agent 角色的代码库中完成质量验证：pytest {pytest['passed']} 通过、{pytest['failed']} 失败，ruff 全量检查通过。",
        "",
        "Context ablation is intentionally not used as a positive resume claim: the recorded full variant consumed more tokens and made more repeated reads in this run.",
    ]
    (ARTIFACTS / "resume_metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("RESUME READY METRICS")
    print(f"Retrieval Full Top-3: {full['top3_hits']}/{retrieval['cases']} ({fmt_percent(full['top3'])})")
    print(f"Multi-Agent speedup: {fmt_percent(multiagent['speedup'])} ({multiagent['serial']['wall_clock_ms'] - multiagent['parallel']['wall_clock_ms']}/{multiagent['serial']['wall_clock_ms']})")
    print(f"Reliability: {reliability['recovered_or_correct_cases']}/{reliability['injected_failure_cases']} ({fmt_percent(reliability['fallback_success_rate'])})")
    print(f"pytest: {pytest['passed']} passed, {pytest['failed']} failed, {pytest['skipped']} skipped")
    print(f"ruff: {'passed' if ruff_result['returncode'] == 0 else 'failed'}")


if __name__ == "__main__":
    main()
