# CodeCub 2.0 — Phase 1 Baseline

> Phase: Long-Horizon Runtime（Repository Stabilization & Closure, Phase 1.5）
> 只记录 Phase 1 工程收口事实；不进入 Phase 2（Context Compiler / Memory 2.0）。

## 记录信息

- Branch: `codex/agent-experiments`
- Phase 1 Commit: `45fba84`（feat: add long-horizon runtime watchdog）
- Phase 1.5 Closure Commit: `8307a01`（test: phase 1.5 closure — long-run >80 step proof and environment-aware skips）
- Parent: `45fba84` → `c699488` → `dd19c51` → `e69e2c9` → `d5600f0`
- Dirty State: clean（仅 untracked 运行时目录 `.codecub/`、`.workbuddy/`、`scripts/*.py`）
- Date: 2026-08-16

## 历史重建说明（正式实验 commit）

原始正式实验冻结于 `7621201c35bdcdcf9abd59a951adbda768145d58`。Phase 1 开发期间本地 `.git/objects` 意外损坏，
`34f4f8c` / `7621201` 的对象与远程均不可得。源码内容已从备份（`E:\codex_backup\20260815-123400-formal-workspace-path-length`、
`20260815-224827-phase1-long-horizon-runtime`）与工作区恢复，并按逻辑分层重建：

```
d5600f0  Improve agent experiment framework            （Base，远程原 commit）
  ↓
e69e2c9  Add fresh formal experiment holdout            （等价 34f4f8c）
  ↓
dd19c51  Shorten experiment workspace paths on Windows  （等价 7621201）
  ↓
c699488  restore pre-phase1 working tree state          （Phase 1 前未提交工作区固化）
  ↓
45fba84  feat: add long-horizon runtime watchdog        （Phase 1）
  ↓
8307a01  test: phase 1.5 closure ...                    （Phase 1.5 收口）
```

重建 commit 的 SHA 与原始 frozen commit 不同，**不伪造 commit identity**。
原正式实验报告继续记录 Frozen Commit `7621201...`（实验运行时真实记录）。

> Later repository-object loss occurred during Phase 1 development. The source
> state was recovered from preserved working-tree / backup content and recommitted.
> The reconstructed commit SHA differs from the original frozen experiment commit.

## 正式实验 Artifact

`artifacts/experiments/*`（formal-real-agent-r3 / formal-context-ablation / formal-memory-ablation /
formal-recovery / FINAL_FORMAL_EXPERIMENT_REPORT.md）未修改、未重跑、保持只读。

## Runtime 语义

- Interactive Mode: `max_steps=None`（adaptive progress runtime），由 Progress Watchdog 判定 stuck，
  `emergency_cap=500` 兜底（仅防 Runtime Bug / watchdog 漏检 / runaway）。
- Experiment Mode: `effective_step_budget = task.step_budget`（24-step 语义不变），`runtime_mode=experiment` 由
  ExperimentRunner 显式设置。
- Emergency Cap: `DEFAULT_INTERACTIVE_EMERGENCY_CAP = 500`（测试可注入小值）。
- Watchdog: enabled（identical / semantic-read / search / action-error / alternating / verification loop +
  semantic no-progress window；NORMAL → STUCK_SUSPECTED → recovery → NORMAL / STUCK_CONFIRMED）。
- 旧 `repeated_no_progress` / `SEMANTIC_REPEAT_HARD_STOP_THRESHOLD` 硬停机已移除；ProgressWatchdog 是唯一 stuck
  终止决策来源。

## Regression

- watchdog targeted（test_watchdog.py）: pass
- >80-step interactive runtime integration（110 真实 progress steps, tool_steps=110, final_answer_returned）: pass
- emergency cap（注入 cap=8 → emergency_cap_reached; 生产默认 500）: pass
- experiment 24-step（step_budget=24 → step_limit_reached at 24）: pass
- stuck suspected/recovery/confirmed: pass
- safety（path escape / read-only / allowed_tools / approval / ambiguous patch / secret redaction / workspace isolation）: pass
- tests/test_pico.py: pass
- tests/test_experiments.py: pass
- full pytest: pass（2 个环境特定失败已改为合理 skip，见 Known Risks）
- Harness Regression: 12/12
- ruff check .: pass
- secret scan: pass（新增/修改代码无真实 secret）

## Known Risks

1. **重建 commit SHA ≠ 原 frozen SHA**：`34f4f8c` / `7621201` 的历史对象已永久丢失（当前仓库/备份/远程均无），
   重建为等价内容 commit（e69e2c9 / dd19c51）。正式报告中的 Frozen Commit 记录保持不变。
2. **Windows 沙箱环境特定行为**：`test_symlink_path_traversal_is_rejected` 在 symlink 语义未生效时 skip；
   `test_run_task_anchors_paths_to_fixture_copy_even_inside_repo_workspace` 在 safe-delete 策略拦截 fixture
   清理时 skip。两者均已证明与 Phase 1 无关（Before/After 源码对照实验）。
3. **git ref 写入不稳定**：该 Windows 沙箱环境会周期性删除 `.git/refs/heads/codex/` 下的 ref 文件（`main` 顶层
   ref 正常）。提交后如遇 `git log` 报 "no commits yet"，需手动重建
   `.git/refs/heads/codex/agent-experiments`（写完整 40 字符 SHA）或 `git update-ref`。
