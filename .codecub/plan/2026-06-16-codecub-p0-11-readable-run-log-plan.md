# CodeCub P0.11 Readable Run Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify the right-side inspector and make run logs readable by default instead of exposing raw backend JSON.

**Architecture:** Keep the existing `RunInspectorPanel` component structure. Replace only `RunLogSidebar` rendering logic, add i18n labels, and apply CSS overrides that reduce visual density in the inspector.

**Tech Stack:** React, TypeScript, Electron renderer IPC event data, CSS, Vitest.

---

## Scope

- Default run log cards show translated title, detail, and return/result.
- Raw JSON is hidden unless debug mode is enabled.
- Debug mode shows structured fields and raw sanitized event payload in an expandable section.
- Assistant streaming delta events are hidden from the default readable log.
- Inspector status, run trail, and tab controls are visually simplified.

## Files

- Modify: `desktop/src/components/RunLogSidebar.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/RunLogSidebar.test.tsx`
- Modify: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`

## Implementation Steps

- [x] Back up existing files under `E:\codex_backup`.
- [x] Replace raw/default log rendering with translated event cards.
- [x] Add event descriptor logic for session, run status, user message, assistant message, tool result, diff, approval, completion, failure, cancellation, and legacy import events.
- [x] Hide `assistant_delta` from default readable logs.
- [x] Add debug details with structured fields and expandable raw sanitized payload.
- [x] Add Chinese and English labels for readable log event titles and fields.
- [x] Add CSS to simplify inspector header, run trail, tabs, and log cards.
- [x] Add Vitest coverage for default readable logs, hidden assistant deltas, and debug raw payloads.

## Verification

- [x] `npm run test -- RunLogSidebar.test.tsx RunTrail.test.tsx CubStatusChip.test.tsx uiTokens.test.ts`
- [x] `npm run typecheck`
- [x] `npm run test`
- [x] `npm run package:win`
- [x] `desktop/scripts/smoke-packaged.ps1`

## P0.12 Follow-up Fix

- [x] Compact the inspector ready-state layout so the status chip and trail do not read as a cluttered control group.
- [x] Remove bullet markers from the run trail in the right inspector.
- [x] Force visible text for the raw-event debug disclosure in light and dark themes.
- [x] Replace non-ASCII tool-status separators in readable logs with ` - `.
- [x] Add a regression test for the stable tool-status separator.
- [x] Verify with `npm run test -- RunLogSidebar.test.tsx uiTokens.test.ts`.
- [x] Verify with `npm run typecheck`.

## Risks

- Event payload schemas are partially dynamic; unknown events fall back to a generic translated card.
- Debug mode still displays raw payloads, but only inside an expandable section and after secret-field sanitization.
