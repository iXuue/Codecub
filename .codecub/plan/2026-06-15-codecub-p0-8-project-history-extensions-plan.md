# CodeCub P0.8 Project History And Extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add project-scoped chat history resume plus local project-scoped plugin and skill installation to the CodeCub desktop app.

**Architecture:** Electron main process owns trusted filesystem access for project `.codecub/sessions`, `.codecub/skills`, and `.codecub/plugins`. The renderer consumes typed preload APIs, renders compact project-side panels, and starts the existing Python backend with `--resume <session_id>` when a saved chat is selected.

**Tech Stack:** Electron IPC, React, TypeScript, Vitest, existing Python `codecub --app-mode --resume` support.

---

## Understood Requirement

The user wants the current code checked, then wants two Codex-like desktop capabilities:

- Project chat history: after opening a project, list previous project sessions and resume one directly.
- Plugin and skill installation: from the project UI, install local plugin/skill folders into the current project's `.codecub` data area.

Scope is P0.8 local/project-scoped only. Remote marketplaces, extension updates, uninstall, dependency resolution, and plugin runtime execution are intentionally excluded.

## Files And Responsibilities

- Modify `desktop/electron/ipcTypes.ts`: shared IPC types for session summaries, session details, extension entries, and install results.
- Modify `desktop/electron/backendLaunchConfig.ts`: add optional `resumeSessionId` and append `--resume <id>`.
- Create `desktop/electron/projectSessions.ts`: read `.codecub/sessions/*.json`, summarize valid sessions, load user/assistant chat messages.
- Create `desktop/electron/projectExtensions.ts`: list `.codecub/skills` and `.codecub/plugins`, install local folders through validated copy.
- Modify `desktop/electron/main.ts`: add IPC handlers and pass resume id into backend start.
- Modify `desktop/electron/preload.cts`: expose session and extension APIs to renderer.
- Modify `desktop/src/state/chatState.ts`: add a helper to create chat state from loaded session messages.
- Create `desktop/src/components/ProjectHistoryPanel.tsx`: display session history and resume action.
- Create `desktop/src/components/ExtensionsPanel.tsx`: display installed skills/plugins and local install actions.
- Modify `desktop/src/components/ProjectSessionPage.tsx`: place both panels in the project side area.
- Modify `desktop/src/App.tsx`: load sessions/extensions on project entry, resume session, refresh lists after installs.
- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`: add visible copy.
- Modify `desktop/src/styles/app.css`: add compact panel styles.
- Add/modify Vitest tests under `desktop/tests`.

## Task 1: Backend Launch Resume Argument

- [ ] Add a failing test in `desktop/tests/backendLaunchConfig.test.ts`:

```ts
it("passes resume session id when requested", () => {
  const config = buildBackendLaunchConfig("D:/repo", settings("openai"), "", {}, "session-123");
  expect(config.args).toContain("--resume");
  expect(config.args).toContain("session-123");
});
```

- [ ] Implement `buildBackendLaunchConfig(..., resumeSessionId = "")`; when non-empty, append `--resume`, then the id.
- [ ] Run `npm run test -- backendLaunchConfig.test.ts`; expected PASS.
- [ ] Commit with `Add backend resume launch argument`.

## Task 2: Project Session Reader

- [ ] Create `desktop/electron/projectSessions.ts` with:

```ts
export async function listProjectSessions(projectPath: string): Promise<ProjectSessionSummary[]>
export async function loadProjectSession(projectPath: string, sessionId: string): Promise<ProjectSessionDetail>
```

Behavior:

- Read only `<project>/.codecub/sessions`.
- Ignore malformed JSON files.
- Sort newest first by file mtime.
- Count history items and extract the latest user/assistant preview.
- `loadProjectSession` rejects ids containing path separators, then returns user/assistant messages only.

- [ ] Add tests in `desktop/tests/projectSessions.test.ts` for normal listing, malformed file tolerance, message filtering, and path traversal rejection.
- [ ] Run `npm run test -- projectSessions.test.ts`; expected PASS.
- [ ] Commit with `Add project session reader`.

## Task 3: Project Extension Store

- [ ] Create `desktop/electron/projectExtensions.ts` with:

```ts
export async function listProjectExtensions(projectPath: string): Promise<ProjectExtensions>
export async function installProjectExtension(projectPath: string, sourcePath: string, type: "skill" | "plugin"): Promise<InstallProjectExtensionResult>
```

Behavior:

- Skills live in `<project>/.codecub/skills/<id>` and require `SKILL.md`.
- Plugins live in `<project>/.codecub/plugins/<id>` and require `plugin.json`.
- Normalize id from source folder name to lowercase `[a-z0-9._-]`.
- Fail if normalized id is empty or destination exists.
- Copy folder recursively, skipping `.git`, `node_modules`, and `__pycache__`.
- Do not execute plugin code.

- [ ] Add tests in `desktop/tests/projectExtensions.test.ts` for listing, successful skill install, plugin missing manifest failure, and duplicate failure.
- [ ] Run `npm run test -- projectExtensions.test.ts`; expected PASS.
- [ ] Commit with `Add local project extension store`.

## Task 4: IPC And Preload APIs

- [ ] Extend `desktop/electron/ipcTypes.ts` with:

```ts
export type ProjectSessionSummary = { id: string; createdAt: string; updatedAt: string; messageCount: number; preview: string };
export type ProjectSessionMessage = { role: "user" | "assistant"; content: string; createdAt: string };
export type ProjectSessionDetail = { id: string; messages: ProjectSessionMessage[] };
export type ExtensionKind = "skill" | "plugin";
export type ProjectExtension = { id: string; kind: ExtensionKind; name: string; path: string; installedAt: string };
export type ProjectExtensions = { skills: ProjectExtension[]; plugins: ProjectExtension[] };
export type InstallProjectExtensionResult = { canceled: boolean; extension?: ProjectExtension; error?: string };
```

- [ ] Add `sessions:list`, `sessions:load`, `extensions:list`, `extensions:install-skill`, and `extensions:install-plugin` handlers in `desktop/electron/main.ts`.
- [ ] Update `backend:start` to accept optional resume session id.
- [ ] Expose corresponding APIs in `desktop/electron/preload.cts`.
- [ ] Add/adjust IPC type tests if needed.
- [ ] Run `npm run typecheck`; expected PASS.
- [ ] Commit with `Expose project session and extension IPC`.

## Task 5: Renderer State And Panels

- [ ] Add `createChatStateFromSession(detail)` to `desktop/src/state/chatState.ts`.
- [ ] Add tests in `desktop/tests/chatState.test.ts` for loaded user/assistant messages.
- [ ] Create `ProjectHistoryPanel.tsx` with a compact list, empty state, refresh button, and session resume button.
- [ ] Create `ExtensionsPanel.tsx` with installed skills/plugins, install buttons, refresh action, and error display.
- [ ] Update `ProjectSessionPage.tsx` side area to include both panels above diff/log.
- [ ] Update i18n dictionaries and CSS.
- [ ] Run `npm run test -- chatState.test.ts`; expected PASS.
- [ ] Commit with `Add project history and extensions panels`.

## Task 6: App Integration

- [ ] Update `desktop/src/App.tsx`:

- Track `projectSessions`, `sessionError`, `extensions`, and `extensionError`.
- `enterProject(projectPath, resumeSessionId?)` loads session detail when resuming, initializes chat state from that detail, starts backend with resume id, and refreshes sessions/extensions.
- `openRecentProject` still starts a new session unless the user chooses a history item.
- `onResumeSession(sessionId)` calls `enterProject(projectPath, sessionId)`.
- `onInstallSkill` and `onInstallPlugin` call preload install APIs and refresh extension lists.

- [ ] Update `desktop/tests/AppRecentProject.test.tsx` mock API and add a resume-session test.
- [ ] Add component tests for `ProjectHistoryPanel` and `ExtensionsPanel`.
- [ ] Run targeted renderer tests:

```powershell
npm run test -- AppRecentProject.test.tsx ProjectHistoryPanel.test.tsx ExtensionsPanel.test.tsx chatState.test.ts
```

- [ ] Commit with `Wire project history and extension UI`.

## Task 7: Final Verification

- [ ] Run desktop full tests:

```powershell
cd desktop
npm run test
npm run typecheck
npm run build
```

- [ ] Run Python regression tests if backend files were touched. Expected: not needed unless Python changed.
- [ ] Check `git status --short`; only intended files plus pre-existing `desktop/index.html` may be dirty.
- [ ] If verification passes, summarize commits, tests, and remaining non-goals.

## Plan Self-Review

- Spec coverage: P0.8 requirements are covered by Tasks 1-6. Listing/resume is covered by Tasks 1, 2, 4, 5, 6. Local skill/plugin install is covered by Tasks 3, 4, 5, 6. Tests and final verification are covered by Task 7.
- Placeholder scan: no TBD/later placeholders. P1 exclusions are explicit.
- Type consistency: IPC names and types are reused across main, preload, renderer, and tests.
- Maintainability check: filesystem parsing is isolated in Electron helper modules; UI panels remain presentational; `App.tsx` coordinates data flow without changing Python session format.
- Risk check: installation copies local folders only, rejects overwrites, and does not execute plugin code. Chat resume reuses existing `--resume`; it does not invent a second backend session format.
