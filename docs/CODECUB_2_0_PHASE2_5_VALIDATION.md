# CodeCub 2.0 — Phase 2.5 Real-Model Context Continuity Validation

> Phase: Real DeepSeek Context Continuity Validation（Development-only，非正式 Benchmark）
> 状态：COMPLETE

## 记录信息

- Branch: `codex/agent-experiments`
- Phase 2 Parent Commit: `9ef1210`（docs: add CodeCub 2.0 Phase 2 baseline record）
- Validation Fix Commit: `（见 git log）`（fix: improve context continuity under compression）
- Validation Docs Commit: `（见 git log）`（docs: validate real-model context continuity）
- DeepSeek Provider/Model: deepseek / deepseek-v4-flash（native_tools，prompt_cache=false）
- Date: 2026-08-16

## Development Probe Set（evaluation_role=development）

| Probe | ID | Kind | Path | Step Budget |
|---|---|---|---|---|
| A | probe_a_context_continuity | compression_continuity | codecub/runtime.py | 40 |
| B | probe_b_recursive_continuity | recursive_compression | codecub/context_manager.py | 48 |
| C | probe_c_freshness_after_mutation | freshness | codecub/runtime.py | 40 |

3 个 probe 均通过 preflight（fresh workspace / mutation anchor unique / mutation apply /
verifier-after-mutation FAIL / deterministic correct repair PASS / isolation / allowed tools）。
prompt 不泄露 patch；context pressure 来自真实多文件调查（run_shell/read/search 组合）。

## 真实 API Runs（共 6 次，预算 4-6 内）

| Run ID | Probe | Window | Steps | Compressions | Verifier | Stop | 分类 |
|---|---|---|---|---|---|---|---|
| phase25-A-20260816-144855 | A | 12000 | 8 | 9 | - | model_error | C（repo map join crash，已修复） |
| phase25-A-20260816-145434 | A | 12000 | 32 | 53 | **PASS** | model_error | 核心证据：极端压缩下完成修复；最后一步 D（provider 非法 arguments JSON） |
| phase25-A-20260816-145739 | A | 20000 | 17 | 23 | FAIL | model_error | A（edit_decision_exhausted） |
| phase25-A-20260816-145939 | A | 16000 | 15 | 28 | FAIL | model_error | A（edit_decision_exhausted） |
| phase25-B-20260816-150119 | B | 16000 | 10 | 19 | FAIL | model_error | A（edit_decision_exhausted） |
| phase25-C-20260816-150303 | C | 16000 | 8 | 15 | FAIL | model_error | A（edit_decision_exhausted） |

### Probe A — Compression Continuity（核心证据）

- Run phase25-A-20260816-145434：**每步都触发压缩（53 次）**的极端压力下，模型仍：
  - 持续定向定位 `codecub/runtime.py`（read 1-120 / 120-300 / 300-560 / 740-780 行）；
  - 完成真实 edit（workspace_change_count=1, changed_files=[codecub/runtime.py]）；
  - verifier **PASS**（mutation 恢复 baseline）。
  - 证明：Goal retained / Relevant file retained / Model can act & edit / Native conversation valid。
  - 无 context_restart_search（模型压缩后始终定向 read，无从头 broad search）。
- Run 1（-144855）暴露 **C 类 Compiler bug**：`compile_native` 对 `ContextItem` 列表 `"\n".join()` 崩溃（repo map 非空 + 压缩触发时）。已修复（`item.text`）+ 新增 regression 测试 `test_native_compression_with_repo_map_does_not_crash`。
- 其余 run 为 A 类模型策略失败（过度 run_shell 调查，4 次 bounded edit decision 耗尽，未及时 patch）与 D 类 provider 坏输出。

### Probe B — Recursive Compression
- 未取得 compression≥2 且继续 edit 的真实证据（A 类失败：模型调查过深、edit_decision_exhausted）。
- deterministic 层已验证 recursive compression（tests/test_context_compiler.py 48-54）。

### Probe C — Freshness
- 未取得 stale revalidation 的真实证据（A 类失败同上）。
- deterministic 层已验证 freshness 全链路（tests 28-33）。

## Context Continuity Metrics

- Probes With Compression: 6/6（全部触发压缩）
- Probes With Post-Compression Edit: 1（Probe A -145434，verifier PASS）
- Probes With Post-Compression Verification: 1（同上）
- Verifier Passes: 1（Probe A -145434）
- Context Restart Search Count: 0（未观察到压缩后失忆式 broad search）
- Stale Fact Revalidation: deterministic 验证；真实 run 未达（C 类未完成）
- Goal Retention: 高（Probe A 全程持续围绕 memory flag 目标）
- Native Tool Integrity: 无 orphan / 无 message corruption（6 个 run 均未报）

## Token Observations（Probe A -145434，估算值 estimated）

- 早期（压缩 #1）：candidate ≈ 8867 → compiled ≈ 9848
- 后期（压缩 #48）：candidate ≈ 81770 → compiled ≈ 82817
- 说明：context_window=12000 时每步都触发压缩，compiled 略高于 candidate 是因为 compiled 额外包含 pinned/working state/compressed 段；压缩对象是 history 部分。仅描述性报告，不宣称统计意义。

## Context Snapshots

- `artifacts/phase25-probes/phase25-A-20260816-145434/context-snapshots/`（compression-001.json 等）
- 可经 `trace.jsonl` 的 `context_compile_finished` 事件追溯（compilation_metadata 含各层 token 与 compression_count）。
- 不含 secret（脱敏）。

## Bugs Found During Validation

- **Bug**: `compile_native` 在 repo map 非空 + 压缩触发时崩溃（`"\n".join(ContextItem list)`）。
- **Root cause**: native 组装路径直接 join ContextItem 列表，未取 `.text`（legacy 路径正确）。
- **Fix**: `codecub/context_compiler.py` compile_native 改为 `"\n".join(item.text for item in repo_map_items)`。
- **Regression test**: `tests/test_context_compiler.py::test_native_compression_with_repo_map_does_not_crash`。

## Failure Classification（A/B/C/D）

- A（Model Coding / 策略）: 4 个 run（edit_decision_exhausted——模型过度 shell 调查，bounded edit decision 耗尽；既有机制，非 Compiler 问题，不修）。
- B（Context Continuity）: 0（无失忆 / stale evidence / native corruption 证据）。
- C（Compiler Infrastructure）: 1（repo map join，已修复 + regression）。
- D（Provider）: 1（DeepSeek 返回非法 tool arguments JSON，单次，未复现）。

## Safety

Path escape / Read-only / Approval / Allowed tools：probe 全程未触发边界违规（preflight + 运行均 PASS）。
Secret scan：trace / report / snapshot / runs 未发现 DEEPSEEK_API_KEY 或 Authorization 泄漏。
Raw artifact：6 个 run 的 trace/report/task_state 完整保留于 `artifacts/phase25-probes/`。

## Final Verdict

**PHASE_2_REAL_MODEL_VALIDATION = YES（有条件）**

依据：
1. 真实 DeepSeek 上证明：Compression 发生后 Goal retained / Relevant file retained /
   Model can act & edit / Verifier PASS / Native conversation valid（Probe A -145434，
   极端每步压缩压力下完成真实修复）。
2. 无 Context loss 证据（B 类 0 例）；唯一 C 类 bug 已修复并补 regression。
3. 失败集中在 A 类（模型策略 + 既有 bounded edit decision 机制）与 D 类（provider 单次坏输出），
   文档明确 A 类不针对任务调 Compiler。

限制（如实记录）：
- Probe B（recursive≥2）与 Probe C（freshness revalidation）的真实模型证据未取得（A 类失败）；
  其正确性由 deterministic 测试（67 项含 recursive/freshness）覆盖。
- 真实模型 + bounded edit decision 的交互问题（模型调查过深导致 decision 耗尽）为 Remaining Risk，
  属 Phase 2 外既有机制，本阶段不修改。
