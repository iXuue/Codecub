import argparse
from pathlib import Path

from .runner import ExperimentConfig, ExperimentRunner


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run CodeCub real-model experiment suites."
    )
    parser.add_argument(
        "--suite",
        choices=("development", "real-agent", "context", "memory", "recovery"),
        required=True,
    )
    parser.add_argument(
        "--variant", choices=("default", "on", "off"), default="default"
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--output-dir", default="artifacts/experiments")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--approval", choices=("ask", "auto", "never"), default="auto")
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.suite in {"context", "memory"} and args.variant not in {"on", "off"}:
        parser.error("context and memory require --variant on|off")
    result = ExperimentRunner(
        ExperimentConfig(
            suite=args.suite,
            variant=args.variant,
            repeat=args.repeat,
            task_ids=tuple(args.task),
            provider=args.provider,
            model=args.model,
            output_dir=Path(args.output_dir),
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
            approval=args.approval,
            context_window=args.context_window,
            temperature=args.temperature,
            top_p=args.top_p,
            resume=args.resume,
            dry_run=args.dry_run,
        )
    ).run()
    print(result["root"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
