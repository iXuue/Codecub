"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import json
import os
import re
import signal
import shutil
import subprocess
import textwrap
import time
from functools import partial

from .tooling.registry import ToolRegistry
from .workspace import IGNORED_PATH_NAMES, clip

BASE_TOOL_SPECS = {
    "list_files": {
        "schema": {"path": "str='.'"},
        "risky": False,
        "description": "List files in the workspace.",
    },
    "read_file": {
        "schema": {"path": "str", "start": "int=1", "end": "int=200", "force": "bool=False"},
        "risky": False,
        "description": "Read a UTF-8 file by line range.",
    },
    "search": {
        "schema": {"pattern": "str", "path": "str='.'"},
        "risky": False,
        "description": "Search the workspace with rg or a simple fallback.",
    },
    "symbol_search": {
        "schema": {
            "query": "str",
            "path": "str='.'",
            "kind": "str=''",
            "limit": "int=20",
        },
        "risky": False,
        "description": "Search Python definitions in the local structural symbol index.",
    },
    "file_outline": {
        "schema": {"path": "str"},
        "risky": False,
        "description": "List classes and functions from a Python file index.",
    },
    "find_references": {
        "schema": {"symbol": "str", "path": "str='.'"},
        "risky": False,
        "description": "Find syntactic Python call candidates (not semantic LSP references).",
    },
    "retrieve_code": {
        "schema": {"query": "str", "limit": "int=5"},
        "risky": False,
        "description": "Hybrid lexical, AST, and optional semantic code retrieval.",
    },
    "run_shell": {
        "schema": {"command": "str", "timeout": "int=20"},
        "risky": True,
        "description": "Run a shell command in the repo root.",
        "result_contract": {"mode": "process_exit_code"},
    },
    "write_file": {
        "schema": {"path": "str", "content": "str"},
        "risky": True,
        "description": "Write a text file.",
    },
    "patch_file": {
        "schema": {"path": "str", "old_text": "str", "new_text": "str"},
        "risky": True,
        "description": "Replace one exact text block in a file.",
    },
}

# Metadata is policy input, not documentation only.  Side-effecting tools are
# deliberately non-retryable: a timeout does not prove their first attempt did
# not already change the workspace.
for _name, _spec in BASE_TOOL_SPECS.items():
    _read_only = _name in {
        "list_files",
        "read_file",
        "search",
        "symbol_search",
        "file_outline",
        "find_references",
        "retrieve_code",
    }
    _spec.update(
        {
            "side_effect": not _read_only,
            "idempotent": _read_only,
            "retryable": _read_only,
            # Pico-inspired declarative effect metadata.  Approval and
            # execution policy still use the existing ``risky`` field.
            "effect": (
                "read"
                if _read_only
                else "execute"
                if _name == "run_shell"
                else "write"
            ),
            "concurrency_safe": _read_only,
            "timeout_seconds": 20,
            "circuit_breaker": True,
        }
    )
DELEGATE_TOOL_SPEC = {
    "schema": {"task": "str", "max_steps": "int=3"},
    "risky": False,
    "description": "Ask a bounded read-only child agent to investigate.",
}
DELEGATE_TOOL_SPEC.update(
    {
        "side_effect": False,
        "idempotent": True,
        "retryable": False,
        "effect": "external",
        "concurrency_safe": False,
        "timeout_seconds": 120,
        "circuit_breaker": True,
    }
)
DISPATCH_TOOL_SPEC = {
    "schema": {"role": "str", "task": "str", "max_steps": "int=3"},
    "risky": False,
    "description": "Dispatch one bounded Research, Implement, or Review agent.",
    "side_effect": False,
    "idempotent": True,
    "retryable": False,
    "effect": "external",
    "concurrency_safe": False,
    "timeout_seconds": 120,
    "circuit_breaker": True,
}

TOOL_EXAMPLES = {
    "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
    "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
    "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
    "symbol_search": '<tool>{"name":"symbol_search","args":{"query":"binary_search","path":"."}}</tool>',
    "file_outline": '<tool>{"name":"file_outline","args":{"path":"codecub/runtime.py"}}</tool>',
    "find_references": '<tool>{"name":"find_references","args":{"symbol":"binary_search","path":"."}}</tool>',
    "retrieve_code": '<tool>{"name":"retrieve_code","args":{"query":"where is tool validation","limit":5}}</tool>',
    "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
    "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
    "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
    "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
    "dispatch": '<tool>{"name":"dispatch","args":{"role":"research","task":"inspect README.md","max_steps":3}}</tool>',
}


def _native_parameter_schema(value):
    declared = str(value)
    type_name, _, default = declared.partition("=")
    json_type = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
    }.get(type_name, "string")
    schema = {"type": json_type}
    if default:
        schema["default"] = default.strip("'\"")
    return schema


def _native_parameters(tool):
    """Render either the legacy declaration or a JSON-Schema parameters map."""

    declared_parameters = tool.get("parameters")
    if isinstance(declared_parameters, dict):
        parameters = dict(declared_parameters)
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        parameters.setdefault("required", [])
        parameters.setdefault("additionalProperties", False)
        return parameters

    declared_schema = tool.get("schema") or {}
    properties = {
        key: _native_parameter_schema(value)
        for key, value in declared_schema.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": [
            key for key, value in declared_schema.items() if "=" not in str(value)
        ],
        "additionalProperties": False,
    }


def native_tool_definitions(tools):
    """Render the canonical registry as OpenAI-compatible function schemas."""
    definitions = []
    for name, tool in tools.items():
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": _native_parameters(tool),
                },
            }
        )
    return definitions


def build_tool_registry(agent):
    # 现有工具仍然是显式、可审计的；Registry 只替换容器，保留旧的
    # dict-like 读写接口，便于逐步接入 MCP/Plugin 来源。
    tools = ToolRegistry()
    for name, spec in BASE_TOOL_SPECS.items():
        tools.register(name, {**spec, "run": partial(_TOOL_RUNNERS[name], agent)})
    # 子 agent 是刻意做成受限能力的：一旦深度耗尽，
    # 就连 delegate 这个工具都不再暴露给模型。
    if agent.depth < agent.max_depth and agent.multi_agent_enabled():
        tools.register(
            "delegate", {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, agent)}
        )
        tools.register(
            "dispatch", {**DISPATCH_TOOL_SPEC, "run": partial(tool_dispatch, agent)}
        )
    return tools


def tool_example(name):
    return TOOL_EXAMPLES.get(name, "")


def validate_tool_result_contract(tool, result):
    """Validate an explicitly declared business-result contract.

    Unspecified tools intentionally retain historical "function returned" semantics.
    The return value is ``(business_success, failure_reason)``.
    """
    contract = dict((tool or {}).get("result_contract") or {})
    if not contract:
        return True, ""
    mode = str(contract.get("mode", "")).strip().lower()
    text = str(result or "")
    if mode == "process_exit_code":
        match = re.search(r"exit_code:\s*(-?\d+)", text)
        if match is None:
            return False, "missing_exit_code"
        return (int(match.group(1)) == 0, "" if int(match.group(1)) == 0 else "nonzero_exit_code")
    if mode not in {"boolean_field", "required_fields", "structured_result"}:
        return False, "unknown_result_contract"
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return False, "malformed_result"
    if not isinstance(payload, dict):
        return False, "malformed_result"
    if mode == "boolean_field":
        field = str(contract.get("success_field", "")).strip()
        if not field or field not in payload or not isinstance(payload[field], bool):
            return False, "malformed_result"
        return (payload[field], "" if payload[field] else "business_flag_false")
    required = [str(field) for field in contract.get("required_fields", []) if str(field)]
    missing = [field for field in required if field not in payload or payload[field] is None]
    if missing:
        return False, "missing_postcondition"
    return True, ""


def validate_json_tool_arguments(tool, args):
    """Validate an extension tool's JSON-Schema subset at the execution seam.

    MCP schemas are intentionally kept as data on the registry spec.  This
    small validator covers the interoperable object/property/required/type/
    enum subset without adding a runtime dependency; provider-side schemas
    remain available to callers for richer validation when needed.
    """
    schema = (tool or {}).get("parameters") if isinstance(tool, dict) else None
    if not isinstance(schema, dict):
        return
    _validate_json_value(args or {}, schema, "arguments")


def _validate_json_value(value, schema, path):
    if not isinstance(schema, dict):
        return
    if "enum" in schema and value not in schema.get("enum", ()):
        raise ValueError(f"{path} is not an allowed value")
    declared_type = schema.get("type")
    if declared_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError(f"{path} has an invalid properties schema")
        required = schema.get("required") or ()
        missing = [str(name) for name in required if str(name) not in value]
        if missing:
            raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
        for name, item in value.items():
            if name in properties:
                _validate_json_value(item, properties[name], f"{path}.{name}")
        return
    if declared_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_json_value(item, item_schema, f"{path}[{index}]")
        return
    valid = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(declared_type), True)
    if not valid:
        raise ValueError(f"{path} has type {declared_type}")


def validate_tool(agent, name, args):
    args = args or {}
    tool = getattr(agent, "tools", {}).get(name) if hasattr(agent, "tools") else None
    if isinstance(tool, dict) and name.startswith("mcp_"):
        validate_json_tool_arguments(tool, args)

    if name == "list_files":
        path = agent.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return

    if name == "read_file":
        path = agent.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        agent.path(args.get("path", "."))
        return

    if name == "symbol_search":
        if not str(args.get("query", "")).strip():
            raise ValueError("query must not be empty")
        agent.path(args.get("path", "."))
        limit = int(args.get("limit", 20))
        if limit < 1 or limit > 100:
            raise ValueError("limit must be in [1, 100]")
        return

    if name == "file_outline":
        path = agent.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        return

    if name == "find_references":
        if not str(args.get("symbol", "")).strip():
            raise ValueError("symbol must not be empty")
        agent.path(args.get("path", "."))
        return

    if name == "retrieve_code":
        if not str(args.get("query", "")).strip():
            raise ValueError("query must not be empty")
        return

    if name == "dispatch":
        if str(args.get("role", "")) not in {"research", "implement", "review"}:
            raise ValueError("role must be research, implement, or review")
        if not str(args.get("task", "")).strip():
            raise ValueError("task must not be empty")
        return

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return

    if name == "write_file":
        path = agent.path(args["path"])
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        if "content" not in args:
            raise ValueError("missing content")
        return

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = agent.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        return

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        return


def tool_list_files(agent, args):
    path = agent.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item
        for item in sorted(
            path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
        )
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(agent.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(agent, args):
    path = agent.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(
        f"{number:>4}: {line}"
        for number, line in enumerate(lines[start - 1 : end], start=start)
    )
    return f"# {path.relative_to(agent.root)}\n{body}"


def tool_search(agent, args):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = agent.path(args.get("path", "."))

    if shutil.which("rg"):
        # 优先用 rg，因为搜索会非常频繁，搜索延迟会直接影响 agent 控制循环。
        result = subprocess.run(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
            cwd=agent.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip() or result.stderr.strip() or "(no matches)"

    matches = []
    files = (
        [path]
        if path.is_file()
        else [
            item
            for item in path.rglob("*")
            if item.is_file()
            and not any(
                part in IGNORED_PATH_NAMES
                for part in item.relative_to(agent.root).parts
            )
        ]
    )
    for file_path in files:
        for number, line in enumerate(
            file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(agent.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def tool_symbol_search(agent, args):
    results = agent.code_index.symbol_search(
        args["query"],
        args.get("path", "."),
        args.get("kind", ""),
        args.get("limit", 20),
    )
    return (
        "\n".join(
            f"{item.qualified_name}\n  kind: {item.kind}\n  path: {item.path}\n  lines: {item.start_line}-{item.end_line}"
            for item in results
        )
        or "(no matches)"
    )


def tool_file_outline(agent, args):
    symbols = agent.code_index.file_outline(args["path"])
    return (
        "\n".join(
            f"{'  ' if item.parent else ''}{item.kind} {item.qualified_name}  {item.start_line}-{item.end_line}"
            for item in symbols
        )
        or "(no symbols indexed)"
    )


def tool_find_references(agent, args):
    references = agent.code_index.find_references(args["symbol"], args.get("path", "."))
    lines = ["resolution: syntactic"] + [f"{path}:{line}" for path, line in references]
    return "\n".join(lines) if references else "resolution: syntactic\n(no matches)"


def tool_retrieve_code(agent, args):
    result = agent.retriever.retrieve(args["query"], int(args.get("limit", 5)))
    agent.last_retrieval_result = result
    if getattr(agent, "working_state", None) is not None:
        for hit in result.hits:
            agent.working_state.add_relevant_symbol(
                path=hit.path, name=hit.symbol, kind="retrieval"
            )
    lines = [f"strategy: {result.strategy}"]
    for hit in result.hits:
        lines.extend(
            (
                f"{hit.path}:{hit.start_line}-{hit.end_line} score={hit.score:.4f} sources={','.join(hit.sources)}",
                hit.text,
            )
        )
    if result.filtered_out:
        lines.append(
            "filtered_out: "
            + ", ".join(
                f"{item.get('path') or '<service>'}:{item['reason']}"
                for item in result.filtered_out
            )
        )
    return "\n".join(lines) if result.hits else "(no matches)"


def tool_dispatch(agent, args):
    result = agent.orchestrator.dispatch(
        args["role"], args["task"], int(args.get("max_steps", 3))
    )
    return json.dumps({
        "agent_id": result.agent_id,
        "role": result.role,
        "status": result.status,
        "answer": result.answer,
        "tool_steps": result.tool_steps,
        "changed_files": result.changed_files,
        "verification": result.verification,
    }, ensure_ascii=False)


def tool_run_shell(agent, args):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command must not be empty")
    timeout = int(args.get("timeout", 20))
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be in [1, 120]")
    popen_kwargs = {
        "cwd": agent.root,
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "errors": "replace",
        # 这里传入的是过滤后的环境变量，而不是直接继承整个父 shell 环境，
        # 目的是减少敏感信息被意外带进命令执行环境的风险。
        "env": agent.shell_env(),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    started = time.monotonic()
    canceled = False
    while True:
        if getattr(agent, "cancellation_requested", lambda _state: False)(
            getattr(agent, "current_task_state", None)
        ):
            canceled = True
            _terminate_process_tree(process)
            break
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - started >= timeout:
                _terminate_process_tree(process)
                raise
    if canceled:
        raise RuntimeError("shell process cancelled")
    return textwrap.dedent(
        f"""\
        exit_code: {process.returncode}
        stdout:
        {stdout.strip() or "(empty)"}
        stderr:
        {stderr.strip() or "(empty)"}
        """
    ).strip()


def _terminate_process_tree(process, grace_seconds=1.0):
    """Stop a shell and its children without assuming one operating system."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            # ``taskkill /T`` may report success while a console child keeps
            # running.  Use /F here so a cancelled coding run cannot mutate
            # the workspace after its parent shell has exited.
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        try:
            process.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_seconds)
            return
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_seconds)


def tool_write_file(agent, args):
    path = agent.path(args["path"])
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path.relative_to(agent.root)} ({len(content)} chars)"


def tool_patch_file(agent, args):
    path = agent.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    old_text = str(args.get("old_text", ""))
    if not old_text:
        raise ValueError("old_text must not be empty")
    if "new_text" not in args:
        raise ValueError("missing new_text")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must occur exactly once, found {count}")
    path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
    return f"patched {path.relative_to(agent.root)}"


def tool_delegate(agent, args):
    if agent.depth >= agent.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")

    from .runtime import Pico

    child = Pico(
        model_client=agent.model_client,
        model_gateway=agent.model_gateway,
        workspace=agent.workspace,
        session_store=agent.session_store,
        run_store=agent.run_store,
        approval_policy="never",
        max_steps=int(args.get("max_steps", 3)),
        max_new_tokens=agent.max_new_tokens,
        depth=agent.depth + 1,
        max_depth=agent.max_depth,
        read_only=True,
        secret_env_names=agent.secret_env_names,
        shell_env_allowlist=agent.shell_env_allowlist,
    )
    parent_cancel_checker = getattr(agent, "cancel_checker", None)
    if parent_cancel_checker is not None:
        child.cancel_checker = lambda _runtime, _task_state: bool(
            parent_cancel_checker(agent, agent.current_task_state)
        )
    # 委派的目标是“调查”，不是“放权执行”。
    # 子 agent 以只读方式运行、步数更少，最后只把结论文本返回给父 agent。
    child.session["memory"]["task"] = task
    child.session["memory"]["notes"] = [clip(agent.history_text(), 300)]
    return "delegate_result:\n" + child.ask(task)


_TOOL_RUNNERS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "symbol_search": tool_symbol_search,
    "file_outline": tool_file_outline,
    "find_references": tool_find_references,
    "retrieve_code": tool_retrieve_code,
    "dispatch": tool_dispatch,
    "run_shell": tool_run_shell,
    "write_file": tool_write_file,
    "patch_file": tool_patch_file,
}
