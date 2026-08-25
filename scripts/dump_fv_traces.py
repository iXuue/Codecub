"""Dump tool traces for a Fast Validation run root."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for task_dir in sorted(root.glob("memory2_*")):
    for variant in ("off", "on"):
        f = task_dir / f"session-b-{variant}.json"
        if not f.exists():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        ws = data.get("workspace")
        if not ws:
            continue
        runs = list(Path(ws).glob(".codecub/runs/*/trace.jsonl"))
        if not runs:
            continue
        runs.sort(key=lambda p: p.stat().st_mtime)
        trace_path = runs[-1]
        events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tools = [e for e in events if e.get("event") == "tool_executed"]
        stop = next((e for e in events if e.get("event") == "run_finished"), {})
        print(f"=== {task_dir.name} {variant} ({len(tools)} tools) stop={stop.get('stop_reason')} ===")
        for t in tools:
            name = t.get("name")
            args = t.get("args") or {}
            if name == "read_file":
                print(
                    f'  read {args.get("path")} {args.get("start", "?")}-{args.get("end", "?")}'
                )
            elif name in ("search", "symbol_search"):
                pat = str(args.get("pattern", args.get("query", "")))[:55]
                print(f'  {name} path={args.get("path", ".")} pat={pat}')
            elif name == "patch_file":
                print(f'  patch {args.get("path")}')
            elif name == "run_shell":
                print(f'  shell {str(args.get("command", ""))[:75]}')
            elif name == "file_outline":
                print(f'  outline {args.get("path")}')
            else:
                print(f"  {name} {str(args)[:55]}")
        print()
