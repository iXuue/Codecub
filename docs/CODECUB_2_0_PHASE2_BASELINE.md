# CodeCub 2.0 — Phase 2 Baseline

> Phase: Context Compiler（Long-Horizon Runtime 之上的 Context 生命周期管理）
> 只记录 Phase 2 工程事实；不进入 Phase 3（Memory 2.0 / Durable Memory）。

## 记录信息

- Branch: `codex/agent-experiments`
- Parent Phase 1 Commit: `6ddcee7`（docs: add CodeCub 2.0 Phase 1 baseline record）
- Phase 2 Commit: `d2195f9`（test: validate long-horizon context continuity）
- Phase 2 Core Commit: `5fad371`（feat: add task-local context compiler for long-horizon tasks）
- Dirty State: 提交后 clean（仅 untracked 运行时目录）
- Date: 2026-08-16

## Pinned Context

- 内容：user task / project rules（prefix 全文）/ safety（approval + read_only）/
  runtime mode / evidence ledger / checkpoint / relevant memory。
- 稳定 ID：`pinned:user-task`、`pinned:project-rules`、`pinned:safety`、
  `pinned:runtime-mode`、`pinned:evidence-ledger`、`pinned:checkpoint`、
  `pinned:relevant-memory`。
- Dedup：OrderedDict 按 key 去重；同一 key 每轮只渲染一次。
- Overflow：pinned 永不静默删除；即使超预算也保留（允许 compaction_failed 语义）。

## Working State

- 生命周期：Task Start → Task-local → Task Finish（每次 ask() 新建，不写入 durable memory）。
- 字段：Goal / Known Facts（带 provenance + source_hash）/ Changed Files /
  Verification（当前状态 + 少量历史）/ Blockers / Next Step / Relevant Symbols /
  Failed Approaches / Pending Questions。
- 更新原则：由客观 Runtime Event 驱动（workspace_changed → changed_files；
  run_shell → verification；patch rejected → failed_approaches；
  read/symbol_search → relevant_symbols）。
- Bound：facts ≤24、changed_files ≤12、verifications ≤8、blockers ≤6、
  symbols ≤16、failed_approaches ≤6、questions ≤4（集中配置）。

## Recent Verbatim

- 预算：usable_input_budget × 0.38 + 最低 floor（最近 2 个完整 native group）。
- 保护：最近 source read / patch args / test traceback / native tool call /
  tool result / edit decision / verification failure 保留原文，不做 summary。
- Native atomic group：assistant.tool_calls + 对应 tool results 作为一个组；
  禁止 orphan tool message / orphan tool_call；multi-tool batch 保持完整对应。

## Compressed History

- Trigger：`context_utilization = estimated_candidate_input / usable_input_budget`
  ≥ 阈值（默认 0.75；benchmark context_reduction setup 可显式注入）。
- Pipeline：Stage A dedup → Stage B compact bulk（旧巨型 tool output →
  summary + event ref）→ Stage C structured condensation → Stage D recursive。
- Condenser：`HistoryCondenser`（与主 Agent 推理隔离；timeout / provider error /
  malformed 输出 / token accounting / secret redaction / structured validation /
  deterministic fallback）。失败时保留 raw history，绝不 `summary=""` + 删历史。
- 输出结构：Goal-Relevant Findings / Confirmed Facts / Relevant Files /
  Relevant Symbols / Changes / Verification / Failed Approaches /
  Unresolved Blockers / Provenance。
- Recursive：旧 summary 可再次压缩，但保留 goal / blocker / changed files /
  decisions / verification / provenance；summary 大小受
  MAX_COMPRESSED_HISTORY_ENTRIES 约束。

## Repo Map

- 来源：现有 CodeIndex（code_index.py）symbols + imports + calls。
- 选择：task lexical relevance + Working State touched files + relevant symbols。
- 预算：usable_input_budget × 0.10，独立 budget。
- 定位：只提供结构导航；真实修改前仍需 read_file / symbol_search /
  find_references 获取源码。

## Context Budget

- `usable_input_budget = model_context_window - reserved_output_tokens -
  tool_schema_overhead - safety_margin_tokens`。
- window 未知 → conservative fallback（12000），记录 `budget_source=fallback`。
- token-aware（tiktoken，可用时）；禁止退回纯 character 计数。
- benchmark / 测试可显式注入 budget（`budget_source=explicit`）。

## Compression Trigger / Fallback

- 阈值集中配置 `DEFAULT_COMPRESSION_TRIGGER_THRESHOLD = 0.75`。
- 禁止 blind pruning（while tokens > budget: history.pop(0)）。
- 无法满足时优先 `context_compaction_failed` / 保留 pinned，不静默删
  user goal / safety / blocker / latest patch / latest test failure。

## Freshness

- fact 记录 source_hash（复用 memory.file_freshness，不建 parallel system）。
- 文件修改后（workspace_changed / affected_paths）→ 同路径旧 fact 标 stale。
- 每轮 compile 前 `refresh_fact_freshness` 对照当前 hash。
- stale fact 只出现在 "Previously observed, now stale" 段，不当作 current truth；
  压缩不会让 stale fact 复活。

## Regression

- Context Compiler targeted（tests/test_context_compiler.py，67 项）: pass
- >100-step Long-Horizon（两次压缩后仍保留 Goal/Blocker/Changed Files/Symbol）: pass
- Phase 1（watchdog / recovery / emergency cap / experiment 24-step）: pass
- Harness Regression: 12/12
- full pytest / ruff / secret scan: pass

## Known Risks

1. **legacy ContextManager 保留为 deprecated adapter**：feature flag
   `context_compiler=False` 时仍走旧 section-budget 裁剪路径（旧实验 ablation
   与 2 个 ContextManager 行为测试使用）；旧 metrics 字段通过
   `_add_legacy_metadata_compat` 兼容。
2. **压缩质量依赖 deterministic condenser**：当前 HistoryCondenser 默认
   deterministic（不消费主模型 outputs，保证测试稳定）；LLM condenser
   接口已预留（`model_client` 参数 + timeout/fallback），真实 probe 阶段
   可注入，但不会用“针对答案调 Context Compiler”。
3. **context_reduction checkpoint 语义**：compiler 压缩发生时同样创建
   `trigger=context_reduction` checkpoint（与旧语义对齐），evaluator 的
   context_reduction setup 在 compiler 启用时同步注入显式 budget。
