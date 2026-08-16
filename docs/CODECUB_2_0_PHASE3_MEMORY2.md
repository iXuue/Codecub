# CodeCub 2.0 — Phase 3 Memory 2.0

> Base Commit: `f1db8b66726f4bb644b03632b0e98f51c5c9d706`
> Branch: `codex/agent-experiments`
> Phase 3 status: IN PROGRESS (this file is updated as the phase completes)

---

## 1. Old Memory Audit (read-only, before any Phase 3 code)

Audited: `codecub/memory.py` (961 lines), `codecub/runtime.py`, `codecub/context_compiler.py`,
`codecub/task_state.py`, `codecub/run_store.py`, `codecub/code_index.py`, `codecub/watchdog.py`,
`codecub/experiments/metrics.py`, `tests/test_memory.py`, `tests/test_pico.py` (memory sections),
`codecub/metrics.py` (legacy memory experiments / ablation).

### 1.1 Current responsibilities of `memory.py` (all in one module)

| Responsibility | Where | Notes |
|---|---|---|
| Task summary | `working.task_summary` / `set_task_summary` | written by runtime `ask()` from user message |
| Recent files | `working.recent_files` / `remember_file` | written on read/write/patch via `update_memory_after_tool` |
| Episodic notes | `episodic_notes` / `append_note` | read summaries + process notes (`record_process_note_for_tool`) |
| File summaries | `file_summaries` / `set_file_summary` | written on read_file; freshness = file hash at write time |
| Durable store | `DurableMemoryStore` (.codecub/memory/MEMORY.md + topics/*.md) | regex-driven promotion |
| Retrieval | `retrieval_candidates` / `retrieval_debug` | keyword/tag/path scoring + per-kind budgets |
| Dedupe | `_dedupe_retrieval_candidates*` | text / identity / same-file |
| Ranking | `score_memory_candidate` | path 120 > source 100 > tag 80 > keyword 10 |
| Freshness | `file_freshness` / `invalidate_stale_file_summaries` | stale summaries are **dropped**, not surfaced |

### 1.2 Audit answers (spec §7)

1. **Working Memory 写在哪里？** runtime `update_memory_after_tool()` / `record_process_note_for_tool()` /
   `ask()` 开头 `set_task_summary`；落盘到 `session["memory"]`。
2. **Context Compiler WorkingState vs memory working 重复？** 是。`working.task_summary`≈WorkingState.goal；
   `recent_files`≈WorkingState.relevant_symbols(kind=file)。两套平行 task state，存在漂移风险。
3. **`recent_files` vs Relevant Symbols 重复？** 是。两者都记录接触过的文件；形状不同、内容重叠。
4. **`file_summaries` 谁生成？** runtime `update_memory_after_tool` 在 read_file 时
   `set_file_summary(path, summarize_read_result(result))`。
5. **file summary freshness 失效？** `evaluate_resume_state()` 里 `invalidate_stale_file_summaries()`
   对比当前 hash；写文件时 `invalidate_file_summary`。stale 条目被直接删除（丢失 location-hint 价值）。
6. **episodic notes 谁写入？** read 摘要 note + `record_process_note_for_tool` 的 process note +
   旧实验脚本直接 `append_note`。
7. **哪些是短期 process noise？** `kind="process"` 的 tool partial_success/error/rejected 提示、
   read 摘要 note —— 每步瞬态，绝大多数不应进入长期记忆。
8. **DurableMemoryStore 什么时候写？** run 结束（success/limited/stuck/finalization/emergency 五个出口）
   调 `promote_durable_memory()`，仅当用户消息命中 intent pattern 且 final answer 行命中 line patterns。
9. **durable promotion 谁触发？** runtime 正则（DURABLE_MEMORY_INTENT_PATTERN + LINE_PATTERNS），
   无证据约束、无候选过滤（只有 subject-key 去重/supersede）。
10. **durable topic 当前？** project-conventions / key-decisions / dependency-facts / user-preferences。
11. **retrieval query 如何构造？** 只有原始 `user_message`（`_pinned_extra` 每轮 prompt 都跑）。
12. **retrieval scoring？** path/source/tag/keyword 基础分 + matched term 数量；排序 (score, created_at, index)。
13. **file_summary/episodic/durable 怎么竞争 Top-K？** 同一 pool → dedupe → per-kind budget
    (file_summary 1, durable 1, episodic+process 2) → 总 limit 3。
14. **Memory 如何进 Context Compiler？** `pinned:relevant-memory` → Pinned 层（每轮重建，limit=3）。
15. **retrieval 会覆盖 live evidence？** 不会显式覆盖；但 stale 摘要被直接丢弃，模型看不到
    "以前在这找到过 X" 的定位提示，丧失 stale-hint 价值。
16. **旧 memory state 存在哪？** `session["memory"]`（session JSON）+ `.codecub/memory/` markdown。
17. **session reload 如何恢复？** `Pico.from_session` → `LayeredMemory(session["memory"])` →
    `normalize_memory_state`；resume 时重查 file summary freshness。
18. **纯 backward-compat 字段？** `task` / `files` / `notes` / `next_note_index` / `durable_topics`
    （都是 working/episodic 的别名）。
19. **旧 metrics 依赖当前 schema？** `codecub/metrics.py` 的 memory 实验（memory_on/off/irrelevant、
    ablation v2、noise injection 直接操作 `agent.memory.state`）；`experiments/metrics.py`
    （memory_recall_count / file_summary_recall_count / stale_memory_rejection_count）；
    `tests/test_memory.py`（14 个测试）。
20. **Phase 3 兼容旧 session？** 保留 `session["memory"]` 旧 schema 读取；v1 markdown durable
    迁移进 v2 JSONL；`LayeredMemory` 保留为 legacy adapter；新增 `memory_schema_version`；
    migration 幂等、损坏安全。

### 1.3 旧 Memory 最大问题

- `memory.py` 是单一无限职责类：task summary + recent files + episodic + file summaries +
  durable + retrieval + dedupe + ranking + freshness 全在一个模块。
- `memory["working"]` 与 Phase 2 WorkingState 双写漂移。
- stale file summary 被删除而非作为 STALE hint 保留。
- retrieval 每轮 prompt 都跑且 query 只有 user_message（无 Working State 信号）。
- durable promotion 纯正则、无证据约束、无候选过滤、无 provenance。
- Memory 以 pinned 形式进 Context（每轮重建，量小但有架构歧义，无独立 token 预算口径）。

---

## 2. Memory 2.0 Architecture (target)

```
                    Memory 2.0

           ┌────────────┼─────────────┐
           ▼            ▼             ▼
    Working State   Evidence Store   Durable Memory
      Task-local       可验证代码证据      长期稳定知识
      (Phase 2)          新增              重构
           │             │                │
           └─────────────┼────────────────┘
                         ▼
                   Memory Retriever
                         │
                  bounded Top-K
                         │
                  Context Compiler
                         │
                       Model
```

### 2.1 第一原则

`Live Workspace > Evidence Store > Durable Memory`

Memory 是**索引与导航，不是 Source of Truth**。stale 时只能返回
"以前在这发现过 X，重新 read"，不能说 "当前代码一定是 X"。

### 2.2 模块结构（codecub/memory_v2/）

| File | Responsibility |
|---|---|
| `secrets.py` | secret 检测与清洗（Extraction/Consolidation/Persistence/Retrieval/Debug 全链路） |
| `storage.py` | 原子 JSONL/JSON 持久化、损坏安全加载 |
| `observability.py` | trace event 名常量 + metrics key 常量 |
| `evidence.py` | `EvidenceRecord` / `EvidenceStore`（fresh/stale/missing/superseded，bounded） |
| `durable.py` | `DurableMemoryRecord` / `DurableMemoryStore`（active/superseded/retired/rejected，provenance） |
| `extraction.py` | `MemoryCandidate` / `MemoryExtractor`（trigger、候选过滤、证据约束） |
| `consolidation.py` | `MemoryConsolidator`（NEW/DUPLICATE/MERGE/SUPERSEDE/CONFLICT/REJECT） |
| `retrieval.py` | `MemoryRetriever` / `MemoryRetrievalResult`（layered ranking、Top-K、token budget、diversity、cache、progress-aware） |
| `migration.py` | `MemoryMigration`（v1 → v2，幂等，损坏安全，保留旧数据） |
| `__init__.py` | `MemoryV2` facade（编排 store/retriever/extractor/consolidator/migration + counters） |

### 2.3 Feature Flags

- `memory_v2: True`（master；Memory 2.0 整体开关，用于 A/B）
- `evidence_memory: True`（Evidence Store 子开关）
- `durable_memory: True`（Durable Memory 子开关）
- 旧 `memory` / `relevant_memory` 语义：`memory=False` → 完全 Memory OFF（兼容旧实验）；
  `memory_v2=False` → 走旧 v1 retrieval 路径（legacy mode），绝不双注入。

### 2.4 Storage

`.codecub/memory/v2/`:
- `evidence.jsonl` — EvidenceRecord
- `durable.jsonl` — DurableMemoryRecord
- `index.json` — `{schema_version: 2, generation, migrated, migration_marker, counts}`

写入：temp file → atomic replace；JSONL 逐行 append（先写再 flush）。损坏文件 → 安全降级（空 store + corruption 标记），不影响 Agent 启动。

### 2.5 与 Phase 2 Working State 的边界

- Context Compiler `WorkingState` 是当前 Task 的**唯一权威状态**。
- runtime 主路径（memory_v2 ON）不再用 v1 `working.task_summary/recent_files` 作为任务真相；
  checkpoint key_files 改用 WorkingState + EvidenceStore 路径（保留 recent_files 兼容镜像）。
- `LayeredMemory` 保留为 legacy compatibility adapter（v2 OFF 时原样工作，旧测试/实验不破坏）。

---

## 3. Phase 3 Execution Checklist (updated as work lands)

- [ ] Audit + design (this doc)
- [ ] memory_v2 package foundations (secrets/storage/observability)
- [ ] Evidence Store
- [ ] Durable Memory
- [ ] Extraction + Consolidation
- [ ] Retrieval
- [ ] Migration + MemoryV2 facade
- [ ] runtime integration
- [ ] context_compiler integration
- [ ] deterministic tests
- [ ] full regression (pytest / test_pico / test_experiments / harness / ruff / secret scan)
- [ ] Fast Validation 6 runs (3 tasks × Memory OFF/ON)
- [ ] Final audit report
