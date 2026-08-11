# CodeCub P0.10 UI History and Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the desktop UI clearer and controllable by fixing mojibake, adding Home navigation, adding user-created/deletable chat records, and reducing text-blurring glass effects.

**Architecture:** Keep the current Electron IPC and React component structure. Add focused session-management IPC methods in `projectSessions.ts`, expose them through preload, and keep chat history UI behavior inside the project sidebar.

**Tech Stack:** Electron main/preload IPC, React, TypeScript, Vitest, CSS.

---

## Scope

- Fix corrupted Chinese UI strings and corrupted window control symbols.
- Add a Home button on the project session page.
- Add New chat and Delete chat actions to the project chat history panel.
- Hide empty legacy/backend sessions unless they were explicitly created by the desktop UI.
- Keep malformed session files ignored.
- Reduce blur and transparency effects on dense text surfaces.

## Files

- Modify: `desktop/electron/ipcTypes.ts`
- Modify: `desktop/electron/projectSessions.ts`
- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/preload.cts`
- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/components/Toolbar.tsx`
- Modify: `desktop/src/components/ProjectSessionPage.tsx`
- Modify: `desktop/src/components/ProjectSidebar.tsx`
- Modify: `desktop/src/components/ProjectHistoryPanel.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Modify: `desktop/src/styles/app.css`
- Modify tests for project sessions, app flow, history panel, and project layout.

## Implementation Steps

- [x] Back up existing files under `E:\codex_backup`.
- [x] Add `createProjectSession` and `deleteProjectSession` IPC result types.
- [x] Replace project session listing logic so empty non-desktop sessions are hidden.
- [x] Add desktop-created empty session creation with `created_by: "codecub-desktop"`.
- [x] Add safe deletion by validated session id.
- [x] Wire `sessions:create` and `sessions:delete` through Electron main and preload.
- [x] Track `activeSessionId` in `App.tsx`.
- [x] Add new chat and delete chat handlers in `App.tsx`.
- [x] Add Home navigation from project session page.
- [x] Redesign `ProjectHistoryPanel` actions and readable titles.
- [x] Replace corrupted Chinese i18n strings.
- [x] Replace corrupted window control characters.
- [x] Add readability CSS overrides that reduce blur on dense content.
- [x] Add/update tests for session filtering, creation, deletion, history panel actions, and App new-chat flow.

## Verification

- [x] `npm run test -- projectSessions.test.ts ProjectHistoryPanel.test.tsx AppRecentProject.test.tsx ProjectSessionPageLayout.test.tsx`
- [x] `npm run typecheck`
- [x] `npm run test`
- [x] `npm run package:win`
- [x] `desktop/scripts/smoke-packaged.ps1`

## Risks

- Deleting a chat intentionally removes a session JSON file; the renderer must require confirmation before invoking IPC.
- Existing historical sessions with valid messages still appear, but now use a human title instead of the raw id.
- Visual polish remains incremental; this pass prioritizes readability and control over a full redesign.
