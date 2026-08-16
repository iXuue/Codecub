# CodeCub 2.0 — Phase 2.6 Baseline：Adaptive Edit Control & Context Stabilization

> Phase: Adaptive Edit Control + Compression Hysteresis + 同口径 Token Metrics
> 状态：COMPLETE（Development Probes 4 runs，总上限内）
> 只记录 Phase 2.6 工程事实；不进入 Phase 3（Memory 2.0）。

## 记录信息

- Branch: `codex/agent-experiments`
- Phase 2.5 Parent Commit: `cf9b168`（docs: validate real-model context continuity）
- Backup Tag: `phase2.5-baseline-20260816`
- Phase 2.6 Commit: `（见 git log，本文件提交）`
- DeepSeek Provider/Model: deepseek / deepseek-v4-flash（native_tools，prompt_cache=false）
- Date: 2026-08-16

## 1. Adaptive Edit Control（EditDecisionWatchdog）

### 变更

- **删除小固定 hard-stop**：`EDIT_DECISION_ATTEMPT_BUDGET = 4` 与
  `EDIT_EVIDENCE_RETRY_BUDGET = 2` 已移除（runtime.py 不再出现
  `edit_decision_exhausted` 停机）。
- **新组件** `codecub/edit_decision.py::EditDecisionWatchdog`：逐条分类
  need_evidence 请求：
  - progress：新文件 / 新 range / 新 symbol / 新 search /
    mutation 后同文件 hash 变化的 re-read（stale -> revalidation -> fresh）；
  - no progress：完全相同的 read/search/symbol，且无 workspace change、
    文件 hash 未变。
- **执行控制**：progress -> 受控执行（read_file / search / symbol_search，
  安全边界不变）；no progress -> 拒绝（retry notice，含原因），并把合成
  rejected 事件喂给主 ProgressWatchdog，使重复 evidence 也能走
  suspected -> recovery -> stuck_confirmed。
- **原则**：是否继续由“真实进展”决定，不由“第几次 decision”决定。
- 观测：`edit_decision_watchdog` snapshot（total / edits / evidence /
  executed / rejected_no_progress / no_progress_streak）写入 report；
  trace 新增 `edit_decision_no_progress` 事件；`evidence_request_count`
  等 planning 字段保留。

### 顺带修复（Probe B/C 暴露的 Runtime bug）

- **native 空 final 不算成功完成**：legacy 路径本就拒绝空 `<final>`，native
  路径此前把空文本 final 当作成功（Probe B/C 首次运行均出现
  `final_answer_returned` 但 final_answer 为空）。已改为 retry
  （`model returned an empty final response`，notice 以 user 消息注入
  native 对话），与 legacy 语义对齐，避免误导性 completed 记录。
- **native retry notice 可见性**：被拒绝的 edit decision / evidence 请求
  原先只写入 session history（native 模型看不到）；现以 user 消息追加到
  native_messages。

## 2. Compression Hysteresis（context_compiler.py）

### 变更

- 单 threshold（`utilization >= 0.75 -> compress`）升级为：
  `HIGH_WATERMARK(0.80) -> compress -> TARGET_WATERMARK(0.55, headroom) ->
  重新涨到 HIGH 才再次压缩`。
- 新增参数：`high_watermark` / `target_watermark` / `min_reclaim_tokens` /
  `min_reclaim_ratio` / `min_new_history_entries`（默认 4）。
- 新增观测：`steps_since_last_compression` / `compression_skipped_no_gain` /
  `compression_thrashing_detected` / `last_compressed_history_len` /
  `last_compressed_span_fingerprint` / `last_compiled_context_tokens` /
  `last_compression_compiled_tokens`（`hysteresis_snapshot`，写入 metadata）。
- **per-mode 隔离**：legacy 与 native 两条管线在同一个 runtime 回合都会跑
  （native 模式下 legacy compile 仍为 observability 执行），hysteresis 状态
  按模式隔离（`_HysteresisState`），避免两侧 history 长度口径互相污染。
- **有界性**：超 HIGH 但被 hysteresis 跳过时，仍执行分区 + 压缩保持
  model-visible 有界（不计为 compression / 不触发 checkpoint），避免两次
  计次压缩之间上下文无界增长。
- **span fingerprint**：最旧 1/3 history 的稳定 hash，识别“同一批 history
  被反复重压”（exact-replay skip + observability）。
- `compression_count` / `compressed_summaries` / hysteresis 状态 task-local：
  `ContextCompiler.reset_run_state()` 在每次 ask() 开始时清零。

### 顺带修复（同口径 metrics 暴露的 legacy 分区 bug）

- `_partition_history` 原 `or not older` 条件会让整段 history 留在 recent，
  legacy 压缩空转（compiled ≈ raw）。已改为与 native 一致的有界 recent
  （至少保留最新 1 条，预算内吸收，超出进 older 压缩）。

## 3. 同口径 Token Metrics

新增（raw / compiled 覆盖同一范围，ratio 才可算）：

- `raw_model_visible_tokens` / `compiled_model_visible_tokens`
- `context_tokens_reclaimed` / `context_reduction_ratio`
- `raw_history_tokens`（已有）/ `compiled_history_tokens` /
  `history_reduction_ratio`
- `provider_actual_input_tokens`（runtime 拿到 usage 后回填，与估计同场对比）

修复：native 未压缩分支 compiled 缺失（原为 0）；native `raw_history_tokens`
改为 `_estimate_native_tokens`（与 compiled 同口径）。

## 4. Regression（deterministic）

- `tests/test_edit_decision.py`（新增 11 项）：EditDecisionWatchdog 分类 /
  新 evidence 超过旧 4+2 限制 / 重复 evidence -> suspected -> recovery ->
  confirmed / native 空 final 拒绝。
- `tests/test_context_compiler.py`（新增 8 项）：hysteresis 同 span 跳过 /
  新条目后再压 / metadata 可观测 / reset_run_state / per-mode 隔离 /
  同口径 token metrics（legacy + native 压缩/未压缩）。
- `tests/test_watchdog.py`（新增 2 项）：mutation 后 hash 变化 re-read =
  progress（Probe C 单元证据）/ snapshot 上报。
- full pytest：**351 passed, 2 skipped, 4 deselected**（deselected 为
  slow/benchmark 标记）。
- Harness Regression v2：**12/12**。
- ruff check：pass（`ruff format` 非项目门槛，基线即 61 文件不满足）。
- Secret scan：源码与 phase26-probes artifacts 均未发现泄漏。

## 5. Real-Model Development Probes（evaluation_role=development，共 4 runs = 上限）

| Run ID | Probe | Steps | Compressions | Edit Decisions | Evidence Executed | Evidence Rejected | Stop | Verifier | 分类 |
|---|---|---|---|---|---|---|---|---|---|
| phase26-B-20260816-165333 | B | 10 | 6 | 2 | 1 | 0 | final_answer_returned（空 final，已修） | FAIL | A/D |
| phase26-C-20260816-165920 | C | 10 | 6 | 2 | 1 | 0 | final_answer_returned（空 final，已修） | FAIL | A/D |
| phase26-B-20260816-170246 | B | 28 | 15 | **35** | **14** | **17** | model_error（provider 非法 arguments JSON） | FAIL | A/D |
| phase26-C-20260816-170246 | C | 40 | 17 | 33 | 12 | 9 | step_limit_reached | FAIL（模型改错文件） | A |

### 关键证据（B#2）

- **35 个 edit decision 未触发任何 hard-stop**（旧机制在第 4 次即
  `edit_decision_exhausted` 死亡）。
- **14 次 evidence 真实执行**（旧上限 2 次）；**17 次完全重复 evidence 被
  拒绝**（progress-aware），trace 记录 `edit_decision_no_progress`。
- 主 Watchdog 在重复 evidence 下触发 **suspected(step 32, 44) -> recovery
  ×2 -> 1 次 recovery 成功**；运行未死于 stuck，而是继续到 provider 错误。
- native 对话全程无 orphan / 无 tool_call_id 损坏；compression 后模型继续
  定向 read（context_manager.py 各区间 + runtime.py），无 broad restart
  search。
- 压缩频率：15 次 / 28 步（含 legacy observability 管线的计数），hysteresis
  skip 32 次；对比 Phase 2.5 Probe A 的 53 次 / 8 步（每步都压）。

### Probe C（freshness）

- 真实 run 未观察到完整 stale -> revalidation -> fresh 循环（模型从未在
  mutation 后复读同路径——B#2 改错文件、C#2 未触及 runtime.py mutation
  区域），该链路由 deterministic 测试覆盖：
  `test_rereread_after_file_change_is_progress`（watchdog 单元）+
  EditDecisionWatchdog hash-change 单元测试。
- C#2 中模型做出 1 次真实 edit（codecub/context_manager.py，非 mutation
  目标文件 runtime.py）——A 类（模型策略：定位偏差），非 Runtime bug。

### 失败分类

- A（Model Coding / 策略）：模型过度调查证据（33-35 次 decision 未及时
  patch）、改错文件、空 final。**不修**（Phase 2.5 同判例：A 类不针对任务调
  Runtime/Compiler）。
- B（Context Continuity）：0（无失忆 / stale evidence 误用 / native
  corruption）。
- C（Runtime/Compiler Infrastructure）：1 个已修（native 空 final 当作
  成功）+ 1 个已修（native retry notice 不可见）。
- D（Provider）：1（DeepSeek 返回非法 native tool arguments JSON，
  B#2 终点；Phase 2.5 同族问题，未复现到稳定）。

## 6. Phase 2.6 最小 Gate 核对

- [x] 不再有 edit decision = 4 的小固定 hard-stop（35/33 次 decision 无
  exhausted）
- [x] 新 evidence 可以继续超过旧 4 次限制（14/12 次执行，旧上限 2）
- [x] 重复 evidence 能 suspected -> recovery ->（deterministic 层
  stuck_confirmed）
- [x] Phase 1 Watchdog / emergency cap / safety 不退化（351 项全绿 +
  safety invariants）
- [x] compression hysteresis 生效（压缩频率下降、skip 计数、per-mode）
- [x] 合理窗口下不再明显每步 compression（B#2 15/28 vs 旧 53/8）
- [x] raw / compiled token metrics 同口径（ratio 可算）
- [x] Probe B 不再 edit_decision_exhausted
- [~] Probe C 真实 stale -> revalidation -> fresh：deterministic 覆盖；
  真实 run 未触发（A 类模型路径未走到）——如实记录
- [x] full regression / Harness 12/12 / ruff / secret scan 全绿

## 7. Final Verdict

**PHASE_2_6_ADAPTIVE_CONTROL = YES**

依据：
1. 真实 DeepSeek 上证明：取消小固定 hard-stop 后，模型可以进行远超旧上限的
   edit decision（35 次）与 evidence（14 次执行 / 17 次 progress-aware
   拒绝），且不再出现 `edit_decision_exhausted` 停机。
2. 重复 evidence 的 no-progress 正确流入主 Watchdog（suspected -> recovery
   在真实 run 中触发并成功恢复一次），stuck_confirmed 由 deterministic
   测试覆盖。
3. Compression Hysteresis 生效：压缩频率从“每步都压”降为有节流（含 skip
   计数与 per-mode 隔离），model-visible 保持有界。
4. 同口径 token metrics 落地；顺带修复 native 空 final 成功化与 legacy
   分区空转两个真实 bug（均有 regression 测试）。
5. 无 Context loss / 无 native corruption（B 类 0 例）；D 类 provider 错误
   与 A 类模型策略按 Phase 2.5 判例不修。

限制（如实记录）：
- 4 runs 中 verifier 均未 PASS（A 类：模型未完成正确 patch；D 类：
  provider 非法 JSON 一次）。Probe 的 Runtime/Compiler 目标（不 exhausted、
  evidence 超限继续、重复拒绝、recovery 循环、hysteresis、native 完整性）
  均已取得证据；“模型完成修复”属 A 类，不在本阶段修改范围。
- Probe C 的 stale -> revalidation -> fresh 真实证据未取得（A 类未走到），
  由 deterministic 测试覆盖。

## 8. 停止条件

- 已完成 Phase 2.6 全部目标与 gate；**不进入 Phase 3（Memory 2.0）**。
- 下一阶段才是 Phase 3 — Memory 2.0。

## 9. Artifacts

- `artifacts/phase26-probes/phase26-B-20260816-170246/`、
  `phase26-C-20260816-170246/`（完整 trace / report / task_state /
  context-snapshots / analysis / preflight）。
- 不含 secret（脱敏验证通过）。
