# CodeCub P0.15 Launcher, Empty Chat, and CLI Polish Plan

## Goal

Fix the remaining product-quality gaps reported on 2026-06-17: overly empty layouts, unclear new-chat state, one-click backend launch from the packaged exe, and a more Codex/Claude-Code-like terminal `codecub` interaction.

## Confirmed Problems

- Home and project pages still have large visually empty regions.
- When there are no messages, creating a new chat does not feel different enough.
- Packaged exe should offer a direct one-click path that starts the backend without forcing the user through the project picker.
- Running `codecub` in a terminal currently uses a plain `codecub>` prompt and full-answer print, not an observable activity stream.

## Scope

- Desktop frontend UI and IPC flow only where needed.
- Project session summary/title handling for empty user-created sessions.
- CLI terminal presentation only; no model provider contract changes.
- No backend app-mode JSONL protocol changes for desktop.

## Files

- Modify `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
- Modify `desktop/src/App.tsx`
- Modify `desktop/src/components/WelcomePage.tsx`
- Modify `desktop/src/components/ProjectHistoryPanel.tsx`
- Modify `desktop/src/components/ChatView.tsx`
- Modify `desktop/src/i18n/zh-CN.ts`
- Modify `desktop/src/i18n/en-US.ts`
- Modify `desktop/src/styles/app.css`
- Modify `desktop/electron/projectSessions.ts`
- Modify `codecub/cli.py`
- Modify tests:
  - `desktop/tests/AppRecentProject.test.tsx`
  - `desktop/tests/ProjectHistoryPanel.test.tsx`
  - `desktop/tests/ChatViewLayout.test.tsx`
  - `tests/test_pico.py`

## Implementation Tasks

- [ ] Add P0.15 requirements to the spec.
- [ ] Add a home-page "continue latest project" primary action when recent projects exist. It must call the same project entry path as clicking a recent project, so backend startup happens in one click.
- [ ] Keep "open project" visible as a secondary action.
- [ ] Add compact home-page recent project metadata and denser panel spacing to reduce blankness.
- [ ] Make empty user-created chats visible and obviously selected:
  - Store a stable sentinel title for manually created empty sessions.
  - Render the localized `New chat` title for that sentinel.
  - Show a selected empty-chat banner in the center chat area.
- [ ] Improve project empty state density without turning it into a marketing hero.
- [ ] Replace the terminal CLI prompt with a compact activity-style prompt:
  - Display `CodeCub ready` with cwd/session/model.
  - Prompt as `codecub ›`.
  - During `agent.ask`, print observable status lines such as context/model/tool/diff/completed.
  - Stream assistant deltas when available.
  - Do not expose hidden reasoning.
- [ ] Add regression tests for:
  - Continue-latest button starts the first recent project.
  - Empty manual chat renders localized "New chat" and selected state.
  - CLI interactive output contains activity status and no old `codecub>` prompt.
- [ ] Run desktop focused tests and Python focused tests.
- [ ] Run full tests and typecheck.
- [ ] Package Windows app and run smoke.
- [ ] Launch the packaged app, inspect home/project screenshots, and patch any obvious visual defect before final response.

## Acceptance Criteria

- On the home page, a user with recent projects can start the latest project and backend with one primary click.
- Empty home/project states no longer look like unfilled placeholders.
- Creating a chat with no messages produces a visible, selected "New chat" row and center empty-chat banner.
- `codecub` terminal mode visibly reports what it is doing and uses a more polished prompt.
- No raw chain-of-thought is shown.
- Existing app-mode JSONL remains unchanged.
- Tests, typecheck, package, and packaged smoke pass.
