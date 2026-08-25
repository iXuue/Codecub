"""Real-model serial versus parallel read-only research measurement."""

import json
import threading
import time
from pathlib import Path

from codecub.cli import build_agent, build_arg_parser, load_env_file


TASKS = [
    ("Locate token-budget validation.", "Locate context-window capability validation."),
    ("Locate symbol-index persistence.", "Locate async symbol classification."),
    ("Locate usage aggregation cache accounting.", "Locate usage persistence boundaries."),
    ("Locate workspace ignore rules.", "Locate canonical path normalization."),
    ("Locate app protocol command validation.", "Locate app event serialization."),
]


class CountingClient:
    def __init__(self, client):
        self.client = client
        self.calls = 0
        self.lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _call(self, method, *args, **kwargs):
        with self.lock:
            self.calls += 1
        return getattr(self.client, method)(*args, **kwargs)

    def complete(self, *args, **kwargs):
        return self._call("complete", *args, **kwargs)

    def complete_with_tools(self, *args, **kwargs):
        return self._call("complete_with_tools", *args, **kwargs)

    def stream_complete(self, *args, **kwargs):
        return self._call("stream_complete", *args, **kwargs)


def make_parent():
    args = build_arg_parser().parse_args(
        ["--cwd", ".", "--approval", "auto", "--max-steps", "2", "--max-new-tokens", "128"]
    )
    parent = build_agent(args)
    counter = CountingClient(parent.model_client)
    parent.model_client = counter
    if parent.model_gateway is not None:
        parent.model_gateway.primary = counter
    return parent


def run_variant(parallel):
    parent = make_parent()
    began = time.monotonic()
    errors = 0
    complete = 0
    tool_calls = 0
    for left, right in TASKS:
        try:
            if parallel:
                results = parent.orchestrator.dispatch_many(
                    [("research", left, 2), ("research", right, 2)]
                )
            else:
                results = [
                    parent.orchestrator.dispatch("research", left, 2),
                    parent.orchestrator.dispatch("research", right, 2),
                ]
        except Exception:
            errors += 2
            continue
        complete += sum(bool(result.answer.strip()) for result in results)
        tool_calls += sum(result.tool_steps for result in results)
    return {
        "task_pairs": len(TASKS),
        "research_agents": len(TASKS) * 2,
        "wall_clock_ms": int((time.monotonic() - began) * 1000),
        "model_calls": parent.model_client.calls,
        "tool_calls": tool_calls,
        "result_completeness": complete / (len(TASKS) * 2),
        "error_count": errors,
    }


def main():
    load_env_file(".")
    serial = run_variant(parallel=False)
    parallel = run_variant(parallel=True)
    payload = {
        "same_model": True,
        "same_max_steps": 2,
        "implementation_agents_parallelized": False,
        "serial": serial,
        "parallel": parallel,
        "speedup": (serial["wall_clock_ms"] - parallel["wall_clock_ms"])
        / serial["wall_clock_ms"],
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/multiagent_experiment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
