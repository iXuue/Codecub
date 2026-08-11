# CodeCub P0.13 Inline Chat Activity Stream Plan

## Summary

Move observable run progress out of the crowded right inspector header and into the center chat conversation, matching the Codex-style activity stream requested by the user.

## Requirements

- Remove the right inspector status block containing `C`, ready text, and `上下文 / 模型 / 工具 / 变更 / 完成`.
- Keep the right inspector tabs for `变更 / 日志 / 审批`.
- Show a compact elapsed-time summary inside the chat, such as `已处理 12s`.
- Show translated activity rows in the chat for task received, tool completed, file changes, approval waiting/resolved, model streaming, run completed, run failed, and run canceled.
- Do not render raw backend JSON in the chat activity stream.
- Do not render one activity row per `assistant_delta`; use a single model streaming activity state.
- Derive P0.13 activity from existing backend events only.

## Files

- Modify `desktop/src/components/RunInspectorPanel.tsx`
  - Remove `CubStatusChip`, `RunTrail`, and `deriveRunTrail` usage.
  - Remove `chatState` from `RunInspectorPanel` props.

- Modify `desktop/src/components/ProjectSessionPage.tsx`
  - Pass backend `events` into `ChatView`.
  - Stop passing `chatState` into `RunInspectorPanel`.

- Modify `desktop/src/components/ChatView.tsx`
  - Remove the old top `run-status-strip`.
  - Render `ChatActivityStream` inside the message list.

- Create `desktop/src/components/ChatActivityStream.tsx`
  - Convert backend events into compact user-facing activity entries.
  - Compute elapsed time from `runStatus.startedAt` or event timestamps.
  - Collapse duplicate adjacent entries and limit visual noise.

- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`
  - Add activity stream labels and translated status text.

- Modify `desktop/src/styles/app.css`
  - Add activity stream timeline styles.
  - Tighten right inspector grid rows after removing the status block.

- Modify tests
  - Add `desktop/tests/ChatActivityStream.test.tsx`.
  - Update `ChatView` tests for the new required `events` prop.
  - Update run status test to assert inline activity behavior.

- Modify `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
  - Add P0.13 requirements.
  - Remove conflicting acceptance language that still requires the old status chip/run trail.

## Verification

- Run focused tests:

```powershell
npm run test -- ChatActivityStream.test.tsx ChatViewLayout.test.tsx ChatViewRunStatus.test.tsx ProjectSessionPageLayout.test.tsx
```

- Run typecheck:

```powershell
npm run typecheck
```

- Run full desktop tests:

```powershell
npm run test
```

- Rebuild packaged app after tests pass:

```powershell
npm run package:win
```

- Smoke packaged app:

```powershell
desktop/scripts/smoke-packaged.ps1
```

## Risks

- Current backend events do not always include "tool started" timing, so P0.13 can reliably show completed tool runs and current generic run phase, but not every pre-completion substep.
- If backend `run_status` labels are English, P0.13 must prefer frontend-translated phase labels rather than displaying raw labels in the chat.
- Existing standalone `CubStatusChip` and `RunTrail` components may remain in the codebase temporarily if their tests still cover them, but the project session UI must not render them.
