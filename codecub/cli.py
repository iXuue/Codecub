"""命令行入口。

这个模块负责把“用户怎么启动 pico”翻译成 runtime 能理解的对象：
解析参数、挑模型后端、构建工作区快照、恢复或新建 session，
最后进入 one-shot 或交互式循环。
"""

import argparse
import base64
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

from .app_runner import run_app_mode
from .connections import resolve_effective_connection_profile
from .models import (
    AnthropicCompatibleModelClient,
    OllamaModelClient,
    OpenAICompatibleModelClient,
)
from .model_gateway import GatewayPolicy, ModelGateway
from .runtime import EXECUTION_MODE_MULTI_AGENT, EXECUTION_MODE_SINGLE, Pico, SessionStore
from .workspace import WorkspaceContext, middle

DEFAULT_SECRET_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "MINIMAX_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)

WELCOME_ART = (
    "        /\\___/\\\\",
    "       (  o o  )",
    "       /   ^   \\\\",
    "      /|       |\\\\",
)
WELCOME_NAME = "CodeCub"
WELCOME_SUBTITLE = "local coding agent"
WELCOME_STATUS = "ready for repo work"
HELP_DETAILS = textwrap.dedent(
    """\
    Commands:
    /help                  Show this help message.
    /memory                Show the agent's distilled working memory.
    /memory recall <query> Explain relevant memory recall for a query.
    /session               Show the path to the saved session file.
    /reset                 Clear the current session history and memory.
    /exit                  Exit the agent.
    """
).strip()

CLI_STATUS_LABELS = {
    "building_context": "Building context",
    "model_request": "Requesting model response",
    "model_streaming": "Receiving model response",
    "tool_running": "Running tool",
    "waiting_approval": "Waiting for approval",
    "finalizing": "Finalizing",
    "completed": "Completed",
    "failed": "Failed",
    "canceled": "Canceled",
}


DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_OPENAI_BASE_URL = "https://www.right.codes/codex/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_KIMI_MODEL = "moonshot-v1-8k"
DEFAULT_KIMI_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_ANTHROPIC_BASE_URL = "https://www.right.codes/claude/v1"
LEGACY_SECRET_ENV_NAMES_VAR = "MINI_CODING_AGENT_SECRET_ENV_NAMES"
SECRET_ENV_NAMES_VAR = "CODECUB_SECRET_ENV_NAMES"
PROVIDER_ENV_VAR = "CODECUB_PROVIDER"
MODEL_PROVIDERS = ("ollama", "openai", "deepseek", "kimi", "minimax", "anthropic")
OPENAI_COMPATIBLE_PROVIDER_CONFIG = {
    "openai": {
        "model_env": "OPENAI_MODEL",
        "base_url_env": "OPENAI_API_BASE",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": DEFAULT_OPENAI_MODEL,
        "default_base_url": DEFAULT_OPENAI_BASE_URL,
    },
    "deepseek": {
        "model_env": "DEEPSEEK_MODEL",
        "base_url_env": "DEEPSEEK_API_BASE",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": DEFAULT_DEEPSEEK_MODEL,
        "default_base_url": DEFAULT_DEEPSEEK_BASE_URL,
    },
    "kimi": {
        "model_env": "MOONSHOT_MODEL",
        "base_url_env": "MOONSHOT_API_BASE",
        "api_key_env": "MOONSHOT_API_KEY",
        "default_model": DEFAULT_KIMI_MODEL,
        "default_base_url": DEFAULT_KIMI_BASE_URL,
    },
    "minimax": {
        "model_env": "MINIMAX_MODEL",
        "base_url_env": "MINIMAX_API_BASE",
        "api_key_env": "MINIMAX_API_KEY",
        "default_model": DEFAULT_MINIMAX_MODEL,
        "default_base_url": DEFAULT_MINIMAX_BASE_URL,
    },
}
_DOTENV_VALUES_LOADED_BY_CODECUB = {}


def _is_env_key(value):
    if not value:
        return False
    first = value[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in value)


def _strip_unquoted_comment(value):
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if (
            char == "#"
            and not in_single
            and not in_double
            and (index == 0 or value[index - 1].isspace())
        ):
            return value[:index].rstrip()
    return value.strip()


def _unquote_env_value(value):
    value = _strip_unquoted_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_env_file(path):
    values = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or not _is_env_key(key):
            continue
        values[key] = _unquote_env_value(value)
    return values


def load_env_file(repo_root):
    values = parse_env_file(Path(repo_root) / ".env")
    stale_keys = set(_DOTENV_VALUES_LOADED_BY_CODECUB) - set(values)
    for key in stale_keys:
        if os.environ.get(key) == _DOTENV_VALUES_LOADED_BY_CODECUB[key]:
            os.environ.pop(key, None)
        _DOTENV_VALUES_LOADED_BY_CODECUB.pop(key, None)
    for key, value in values.items():
        previous_value = _DOTENV_VALUES_LOADED_BY_CODECUB.get(key)
        if key in os.environ and previous_value is None:
            continue
        if key in os.environ and os.environ[key] != previous_value:
            _DOTENV_VALUES_LOADED_BY_CODECUB.pop(key, None)
            continue
        os.environ[key] = value
        _DOTENV_VALUES_LOADED_BY_CODECUB[key] = value
    return values


def _effective_provider(args):
    explicit_provider = getattr(args, "provider", None)
    if explicit_provider:
        return explicit_provider
    env_provider = os.environ.get(PROVIDER_ENV_VAR, "").strip().lower()
    if env_provider in MODEL_PROVIDERS:
        return env_provider
    return "openai"


def _effective_model(args, provider):
    # 模型选择优先级：
    # 1. 用户显式传入 --model
    # 2. provider 对应的环境变量
    # 3. 代码里的默认值
    explicit_model = getattr(args, "model", None)
    if explicit_model:
        return explicit_model
    if provider in OPENAI_COMPATIBLE_PROVIDER_CONFIG:
        config = OPENAI_COMPATIBLE_PROVIDER_CONFIG[provider]
        model = os.environ.get(config["model_env"])
        if model:
            return model
        return config["default_model"]
    if provider == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL")
        if model:
            return model
        return DEFAULT_ANTHROPIC_MODEL
    model = os.environ.get("OLLAMA_MODEL")
    if model:
        return model
    return DEFAULT_OLLAMA_MODEL


def _first_env(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _configured_secret_names(args):
    configured_secret_names = set(DEFAULT_SECRET_ENV_NAMES)
    configured_secret_names.update(str(name).upper() for name in args.secret_env_names)
    extra_names = os.environ.get(SECRET_ENV_NAMES_VAR, "")
    if not extra_names.strip():
        extra_names = os.environ.get(LEGACY_SECRET_ENV_NAMES_VAR, "")
    if extra_names.strip():
        configured_secret_names.update(
            item.strip().upper() for item in extra_names.split(",") if item.strip()
        )
    return sorted(configured_secret_names)


def _decode_connection_profile(value):
    """Decode a non-secret desktop profile without trusting its verification fields."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True).decode("utf-8")
        profile = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid connection profile configuration") from exc
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("unsupported connection profile configuration")
    allowed = {
        "schema_version",
        "connection_profile_id",
        "connection_type",
        "api_operator",
        "model_vendor",
        "protocol",
        "response_schema",
        "credential_id",
        "endpoint_verification_status",
        "usage_schema_verification_status",
    }
    return {
        key: value
        for key, value in profile.items()
        if key in allowed and isinstance(value, (str, int))
    }


def _build_model_client(args):
    provider = _effective_provider(args)
    supplied_profile = _decode_connection_profile(
        getattr(args, "connection_profile_b64", "")
    )
    # CLI 只负责把 provider 选择翻译成具体 client。
    # 真正的提示词格式、缓存支持、HTTP 协议差异，都封装在 models.py 里。
    if provider in OPENAI_COMPATIBLE_PROVIDER_CONFIG:
        config = OPENAI_COMPATIBLE_PROVIDER_CONFIG[provider]
        model = _effective_model(args, provider)
        base_url = (
            getattr(args, "base_url", None)
            or os.environ.get(config["base_url_env"])
            or config["default_base_url"]
        )
        api_key = os.environ.get(config["api_key_env"], "")
        return OpenAICompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(
                args, "openai_timeout", getattr(args, "ollama_timeout", 300)
            ),
            connection_profile=resolve_effective_connection_profile(
                base_url, "openai-chat", supplied_profile
            ),
        )
    if provider == "anthropic":
        model = _effective_model(args, provider)
        base_url = (
            getattr(args, "base_url", None)
            or os.environ.get("ANTHROPIC_API_BASE")
            or DEFAULT_ANTHROPIC_BASE_URL
        )
        api_key = _first_env(
            "ANTHROPIC_API_KEY", "RIGHT_CODES_API_KEY", "OPENAI_API_KEY"
        )
        return AnthropicCompatibleModelClient(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=args.temperature,
            timeout=getattr(
                args, "openai_timeout", getattr(args, "ollama_timeout", 300)
            ),
            connection_profile=resolve_effective_connection_profile(
                base_url, "anthropic-messages", supplied_profile
            ),
        )

    model = _effective_model(args, provider)
    host = (
        getattr(args, "host", None)
        or os.environ.get("OLLAMA_HOST")
        or DEFAULT_OLLAMA_HOST
    )
    return OllamaModelClient(
        model=model,
        host=host,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout=args.ollama_timeout,
    )


def build_welcome(agent, model, host):
    width = max(68, min(shutil.get_terminal_size((80, 20)).columns, 84))
    inner = width - 4
    gap = 3
    left_width = (inner - gap) // 2
    right_width = inner - gap - left_width

    def row(text):
        body = middle(text, width - 4)
        return f"| {body.ljust(width - 4)} |"

    def divider(char="-"):
        return "+" + char * (width - 2) + "+"

    def center(text):
        body = middle(text, inner)
        return f"| {body.center(inner)} |"

    def cell(label, value, size):
        body = middle(f"{label:<9} {value}", size)
        return body.ljust(size)

    def pair(left_label, left_value, right_label, right_value):
        left = cell(left_label, left_value, left_width)
        right = cell(right_label, right_value, right_width)
        return f"| {left}{' ' * gap}{right} |"

    line = divider("=")
    rows = [center(text) for text in WELCOME_ART]
    rows.extend(
        [
            center(WELCOME_NAME),
            center(WELCOME_SUBTITLE),
            center(WELCOME_STATUS),
            divider("-"),
            row(""),
            row("WORKSPACE  " + middle(agent.workspace.cwd, inner - 11)),
            pair("MODEL", model, "BRANCH", agent.workspace.branch),
            pair("APPROVAL", agent.approval_policy, "SESSION", agent.session["id"]),
            row(""),
        ]
    )
    return "\n".join([line, *rows, line])


class CliActivityRenderer:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.last_status = ""
        self.delta_seen = False
        self.answer_open = False

    def begin(self, message):
        self.last_status = ""
        self.delta_seen = False
        self.answer_open = False
        print(f"\n> {message}", file=self.stream)

    def handle(self, event_name, payload, runtime, task_state):
        del runtime, task_state
        if event_name == "run_status":
            phase = str(payload.get("phase", ""))
            label = (
                CLI_STATUS_LABELS.get(phase)
                or str(payload.get("label", ""))
                or "Working"
            )
            detail = str(payload.get("detail", "")).strip()
            text = f"{label}: {detail}" if detail else label
            self.status(text)
            return
        if event_name == "tool_executed":
            name = str(payload.get("name", "tool"))
            status = str(payload.get("tool_status", "")).strip()
            text = f"Ran {name}" + (f" ({status})" if status else "")
            self.status(text)
            diff_summary = payload.get("diff_summary", [])
            if payload.get("workspace_changed") or diff_summary:
                count = len(diff_summary) if isinstance(diff_summary, list) else 0
                self.status(
                    "Checked file changes" + (f": {count} file(s)" if count else "")
                )
            return
        if event_name == "assistant_delta":
            text = str(payload.get("text", ""))
            if not text:
                return
            if not self.answer_open:
                print("\nassistant", file=self.stream)
                self.answer_open = True
            print(text, end="", file=self.stream, flush=True)
            self.delta_seen = True

    def status(self, text):
        if not text or text == self.last_status:
            return
        self.last_status = text
        print(f"  - {text}", file=self.stream, flush=True)

    def finish(self):
        if self.answer_open:
            print("", file=self.stream)
            self.answer_open = False
        self.status("Completed")


def decode_cwd_arg(args):
    cwd_b64 = str(getattr(args, "cwd_b64", "") or "").strip()
    if not cwd_b64:
        return str(getattr(args, "cwd", ".") or ".")
    try:
        return base64.b64decode(cwd_b64.encode("ascii"), validate=True).decode("utf-8")
    except Exception as exc:
        raise SystemExit(f"invalid --cwd-b64: {exc}") from exc


def build_agent(args):
    """根据 CLI 参数装配出一个可运行的 Pico 实例。

    为什么存在：
    命令行参数只是字符串和开关，runtime 需要的是已经装配好的对象图：
    model client、workspace snapshot、session store、secret 配置等。
    这个函数负责把“启动参数”翻译成“agent 运行现场”。

    输入 / 输出：
    - 输入：`argparse` 解析后的 `args`
    - 输出：一个新的 `Pico`，或一个从旧 session 恢复出来的 `Pico`

    在 agent 链路里的位置：
    它是整个程序启动链路里最靠近 runtime 的装配点。`main()` 先调它，
    得到 agent 后，后面无论是 one-shot 还是 REPL 模式，都会落到 `ask()`。
    """
    # 这里是 CLI 到 runtime 的装配点：
    # 先整理 secret 名单，再采集工作区快照，随后决定是恢复旧 session
    # 还是创建一个新的 Pico 实例。
    args.cwd = decode_cwd_arg(args)
    workspace = WorkspaceContext.build(args.cwd)
    load_env_file(workspace.repo_root)
    configured_secret_names = _configured_secret_names(args)
    store = SessionStore(workspace.repo_root + "/.codecub/sessions")
    model = _build_model_client(args)
    fallback_name = os.environ.get("CODECUB_FALLBACK_MODEL", "").strip()
    fallback = None
    if fallback_name:
        original = args.model
        args.model = fallback_name
        try:
            fallback = _build_model_client(args)
        finally:
            args.model = original
    gateway = ModelGateway(
        model,
        GatewayPolicy(
            max_concurrency=int(os.environ.get("CODECUB_MODEL_MAX_CONCURRENCY", "2")),
            min_interval_seconds=float(
                os.environ.get("CODECUB_MODEL_MIN_INTERVAL_SECONDS", "0")
            ),
            max_retries=int(os.environ.get("CODECUB_MODEL_MAX_RETRIES", "2")),
            retry_base_seconds=float(
                os.environ.get("CODECUB_MODEL_RETRY_BASE_SECONDS", "1")
            ),
        ),
        fallback=fallback,
    )
    execution_mode = (
        EXECUTION_MODE_MULTI_AGENT
        if getattr(args, "multi_agent", False)
        else EXECUTION_MODE_SINGLE
    )
    session_id = args.resume
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        return Pico.from_session(
            model_client=model,
            model_gateway=gateway,
            workspace=workspace,
            session_store=store,
            session_id=session_id,
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
            execution_mode=execution_mode,
            secret_env_names=configured_secret_names,
        )
    return Pico(
        model_client=model,
        model_gateway=gateway,
        workspace=workspace,
        session_store=store,
        approval_policy=args.approval,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        execution_mode=execution_mode,
        secret_env_names=configured_secret_names,
    )


def build_arg_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="CodeCub local coding agent backend for Ollama, OpenAI-compatible, or Anthropic-compatible models.",
    )
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt.")
    parser.add_argument("--cwd", default=".", help="Workspace directory.")
    parser.add_argument(
        "--cwd-b64",
        default="",
        help="UTF-8 base64 encoded workspace directory for desktop-safe launch.",
    )
    parser.add_argument(
        "--provider",
        choices=MODEL_PROVIDERS,
        default=None,
        help="Model backend to use. Defaults to CODECUB_PROVIDER from .env, then openai.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override. Provider-specific .env variables are used when set.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Ollama server URL. Defaults to OLLAMA_HOST from .env, then localhost.",
    )
    parser.add_argument(
        "--base-url", default=None, help="Provider API base URL for hosted providers."
    )
    parser.add_argument(
        "--connection-profile-b64",
        default="",
        help="Non-secret desktop connection identity JSON encoded as UTF-8 base64.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=300,
        help="Ollama request timeout in seconds.",
    )
    parser.add_argument(
        "--openai-timeout",
        type=int,
        default=300,
        help="OpenAI-compatible request timeout in seconds.",
    )
    parser.add_argument(
        "--resume", default=None, help="Session id to resume or 'latest'."
    )
    parser.add_argument(
        "--approval",
        choices=("ask", "auto", "never"),
        default="ask",
        help="Approval policy for risky tools.",
    )
    parser.add_argument(
        "--secret-env-name",
        dest="secret_env_names",
        action="append",
        default=[],
        help="Extra environment variable names to treat as secrets for trace/report redaction.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Explicit step budget per request. Interactive mode has no fixed budget unless this is set; experiments always use the task step budget.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=1024,
        help="Maximum model output tokens per step.",
    )
    parser.add_argument(
        "--multi-agent",
        action="store_true",
        help="Expose bounded Research/Implement/Review multi-agent tools. Defaults to single-agent mode.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature sent to Ollama.",
    )
    parser.add_argument(
        "--top-p", type=float, default=0.9, help="Top-p sampling value sent to Ollama."
    )
    parser.add_argument(
        "--app-mode",
        action="store_true",
        help="Run machine-readable JSONL app mode for the desktop shell.",
    )
    parser.add_argument(
        "--json-events",
        dest="app_mode",
        action="store_true",
        help="Alias for --app-mode.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if getattr(args, "app_mode", False):
        return run_app_mode(args)

    agent = build_agent(args)
    activity = CliActivityRenderer()
    agent.event_handler = activity.handle

    model = getattr(
        agent.model_client, "model", getattr(args, "model", DEFAULT_OLLAMA_MODEL)
    )
    host = getattr(
        agent.model_client,
        "host",
        getattr(
            agent.model_client, "base_url", getattr(args, "host", DEFAULT_OLLAMA_HOST)
        ),
    )
    print(build_welcome(agent, model=model, host=host))

    if args.prompt:
        # one-shot 模式：只跑一次 ask，不进入 REPL 循环。
        prompt = " ".join(args.prompt).strip()
        if prompt:
            activity.begin(prompt)
            try:
                answer = agent.ask(prompt)
                if activity.delta_seen:
                    activity.finish()
                else:
                    print("\nassistant")
                    print(answer)
                    activity.status("Completed")
            except RuntimeError as exc:
                activity.status("Failed")
                print(str(exc), file=sys.stderr)
                return 1
        return 0

    while True:
        # 交互模式：每次读取一条用户输入，交给同一个 agent，
        # 因此 session history 和 working memory 会跨轮延续。
        try:
            user_input = input("\ncodecub> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/help":
            print(HELP_DETAILS)
            continue
        if user_input == "/memory recall" or user_input.startswith("/memory recall "):
            query = user_input[len("/memory recall") :].strip()
            if not query:
                print("usage: /memory recall <query>")
                continue
            print(agent.memory_recall_debug_text(query))
            continue
        if user_input == "/memory":
            print(agent.memory_text())
            continue
        if user_input == "/session":
            print(agent.session_path)
            continue
        if user_input == "/reset":
            agent.reset()
            print("session reset")
            continue

        activity.begin(user_input)
        try:
            answer = agent.ask(user_input)
            if activity.delta_seen:
                activity.finish()
            else:
                print("\nassistant")
                print(answer)
                activity.status("Completed")
        except RuntimeError as exc:
            activity.status("Failed")
            print(str(exc), file=sys.stderr)
