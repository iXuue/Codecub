"""Reproducible retrieval comparison over fixed repository-local cases."""
import json
import time
from pathlib import Path

from codecub.cli import load_env_file
from codecub.code_index import CodeIndex
from codecub.retrieval import HybridRetriever, RetrievalHit, RetrievalResult

CASES = [
    ("function_definition", "where is run_tool defined", "codecub/runtime.py"),
    ("function_definition", "where is tool_patch_file defined", "codecub/tools.py"),
    ("class_definition", "class definition CodeIndex", "codecub/code_index.py"),
    ("class_definition", "class definition ContextCompiler", "codecub/context_compiler.py"),
    ("call_reference", "calls to append_trace", "codecub/runtime.py"),
    ("call_reference", "symbol_search tool runner", "codecub/tools.py"),
    ("cross_file_dependency", "RunStore usage artifact path", "codecub/run_store.py"),
    ("cross_file_dependency", "CLI creates Pico coding agent", "codecub/cli.py"),
    ("bug_localization", "non idempotent patch retry policy", "codecub/tools.py"),
    ("bug_localization", "workspace path escape safety", "codecub/workspace.py"),
    ("bug_localization", "model gateway fallback retry", "codecub/model_gateway.py"),
    ("bug_localization", "circuit breaker half open recovery", "codecub/resilience.py"),
    ("architecture_question", "context compiler memory integration", "codecub/runtime.py"),
    ("architecture_question", "hybrid semantic retrieval architecture", "codecub/retrieval.py"),
    ("architecture_question", "desktop app protocol", "codecub/app_protocol.py"),
    ("architecture_question", "experiment task runner", "codecub/experiments/runner.py"),
    ("architecture_question", "usage telemetry aggregation", "codecub/telemetry/aggregation.py"),
    ("architecture_question", "task checkpoint state", "codecub/task_state.py"),
    ("architecture_question", "edit decision watchdog", "codecub/edit_decision.py"),
    ("architecture_question", "semantic vector index", "codecub/vector_index.py"),
]

class LexicalRetriever:
    """Repository-local pre-upgrade lexical baseline for the fixed suite."""

    def __init__(self, root):
        self.root = root

    def retrieve(self, query, limit=5):
        began = time.monotonic()
        words = [word.lower() for word in query.split() if len(word) > 2]
        ranked = []
        for path in self.root.glob("codecub/**/*.py"):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for number, line in enumerate(lines, 1):
                matched = sum(word in line.lower() for word in words)
                if matched:
                    ranked.append((matched, path, number, line))
        ranked.sort(key=lambda item: (-item[0], str(item[1]), item[2]))
        return RetrievalResult(
            query=query,
            strategy="lexical",
            hits=[
                RetrievalHit(
                    path.relative_to(self.root).as_posix(), number, number, line,
                    float(matched), ("lexical",), "",
                )
                for matched, path, number, line in ranked[:limit]
            ],
            elapsed_ms=int((time.monotonic() - began) * 1000),
        )


def score(result, expected):
    paths = [hit.path for hit in result.hits]
    return {"top1": paths[:1].count(expected), "top3": int(expected in paths[:3]), "top5": int(expected in paths[:5]), "rr": 1 / (paths.index(expected) + 1) if expected in paths else 0}

def run():
    load_env_file(".")
    root = Path(".").resolve()
    index = CodeIndex(root)
    variants = {
        "baseline_lexical": LexicalRetriever(root),
        "hybrid_ast_lexical": HybridRetriever(root, index, embedding_client=False, reranker=False),
        "full": HybridRetriever(root, index),
    }
    output = {"cases": len(CASES), "variants": {}}
    for name, retriever in variants.items():
        totals = {
            "top1": 0, "top3": 0, "top5": 0, "rr": 0,
            "elapsed_ms": 0, "candidate_files": 0,
        }
        rows = []
        for category, query, expected in CASES:
            result = retriever.retrieve(query, limit=5)
            value = score(result, expected)
            for key in ("top1", "top3", "top5", "rr"):
                totals[key] += value[key]
            totals["elapsed_ms"] += result.elapsed_ms
            totals["candidate_files"] += len({hit.path for hit in result.hits})
            rows.append({"category": category, "query": query, "expected": expected, "paths": [hit.path for hit in result.hits], "strategy": result.strategy, "cache_hit": result.cache_hit})
        output["variants"][name] = {
            "top1": totals["top1"] / len(CASES), "top1_hits": totals["top1"],
            "top3": totals["top3"] / len(CASES), "top3_hits": totals["top3"],
            "top5": totals["top5"] / len(CASES), "top5_hits": totals["top5"],
            "mrr": totals["rr"] / len(CASES),
            "mean_latency_ms": totals["elapsed_ms"] / len(CASES),
            "mean_candidate_files": totals["candidate_files"] / len(CASES),
            "rows": rows,
        }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/retrieval_experiment.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: {metric: value for metric, value in row.items() if metric != "rows"}
                for key, row in output["variants"].items()
            }
        )
    )

if __name__ == "__main__":
    run()
