"""Named, comparable CodeCub ablation configurations."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class AblationSpec:
    name: str
    features: tuple[str, ...]
    description: str


ABLATIONS = (
    AblationSpec("A0", (), "Base runtime without retrieval, context, memory, or subagents."),
    AblationSpec("A1", ("ast",), "AST structural index."),
    AblationSpec("A2", ("lexical", "ast"), "Hybrid lexical + AST retrieval."),
    AblationSpec("A3", ("lexical", "ast", "semantic"), "Adds semantic retrieval."),
    AblationSpec("A4", ("lexical", "ast", "semantic", "reranker"), "Adds reranking."),
    AblationSpec("A5", ("context_compiler",), "Context Compiler."),
    AblationSpec("A6", ("context_compiler", "memory"), "Layered Memory."),
    AblationSpec("A7", ("context_compiler", "memory", "multi_agent"), "Research/Implement/Review orchestration."),
    AblationSpec("A8", ("lexical", "ast", "semantic", "reranker", "context_compiler", "memory", "multi_agent"), "Full CodeCub."),
)


def validate_comparable(records):
    """Reject results that vary model/task/decoding/step budget across stages."""
    fields = ("provider", "model", "task_set", "step_budget", "temperature", "top_p")
    baseline = None
    for record in records:
        signature = tuple(record.get(field) for field in fields)
        if baseline is None:
            baseline = signature
        elif signature != baseline:
            raise ValueError("ablation records must share provider/model/tasks/budget/decoding")
    return True


def write_manifest(path, provider, model, task_set, step_budget, temperature, top_p):
    rows = [
        {
            **asdict(spec),
            "provider": provider,
            "model": model,
            "task_set": task_set,
            "step_budget": step_budget,
            "temperature": temperature,
            "top_p": top_p,
        }
        for spec in ABLATIONS
    ]
    validate_comparable(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ablations": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows
