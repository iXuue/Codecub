# CodeCub

`CodeCub` 是一个本地优先的 coding agent 后端。P0 阶段它先保留命令行和 JSONL app-mode 能力，作为后续 Electron 桌面端的后端执行层。

当前迁移目标：

- 包名：`codecub`
- CLI 命令：`codecub`
- 模块入口：`python -m codecub`
- 会话目录：`.codecub/sessions/`
- 运行工件：`.codecub/runs/<run_id>/`
- 长期记忆：`.codecub/memory/`

旧版 `.pico/` 数据不会在 P0.2 自动导入；导入提示和复制流程属于 P0.4。

## 快速开始

```bash
uv sync
uv run codecub
uv run codecub --cwd /path/to/repo
uv run codecub "inspect the test failures and propose a fix"
python -m codecub
```

App-mode 后端入口：

```bash
uv run python -m codecub --app-mode --cwd /path/to/repo
```

## 模型后端

Ollama：

```bash
ollama serve
ollama pull qwen3.5:4b
uv run codecub --provider ollama --model qwen3.5:4b
```

OpenAI 兼容接口：

```bash
export OPENAI_API_BASE="https://your-api.example/v1"
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="qwen-flash"
uv run codecub --provider openai
```

Anthropic 兼容接口：

```bash
export ANTHROPIC_API_BASE="https://www.right.codes/claude/v1"
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_MODEL="claude-sonnet-4-6"
uv run codecub --provider anthropic
```

## 常用交互命令

- `/help`：查看内置命令
- `/memory`：查看提炼后的工作记忆
- `/memory recall <query>`：查看相关记忆召回说明
- `/session`：查看当前会话文件路径
- `/reset`：清空当前会话状态
- `/exit` 或 `/quit`：退出 REPL

## 安全与持久化

CodeCub 不会默认放开所有高风险动作。shell 执行、文件写入等操作受审批模式控制：

```bash
--approval ask
--approval auto
--approval never
```

每次运行结束后，会在 `.codecub/runs/<run_id>/` 下写出：

- `task_state.json`
- `trace.jsonl`
- `report.json`

这些内容默认只保存在本地，不应随仓库提交。

## 开发检查

```bash
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py -q
uv run ruff check .
```
