# CodeCub P0 Requirements

Date: 2026-06-11
Last updated: 2026-06-15
Status: Draft v0.2
Scope: P0 desktop app requirements

## 1. Requirement Maintenance Rule

This document is the source of truth for CodeCub P0 requirements.

When CodeCub requirements change later, this document must be updated in the same change set. Chat discussion alone is not enough. If implementation, planning, UI design, backend protocol, packaging, storage, or acceptance criteria changes, the relevant section here must be revised before or together with the implementation plan.

## 2. Product Positioning

CodeCub is a desktop code agent for local code repositories. The desktop app is the primary user entry. It launches a local Python agent backend, works inside a user-selected project directory, analyzes code, runs commands, edits files, stores sessions, and shows the execution process.

CodeCub uses a restrained "code pet companion" theme. The pet identity may appear in the product name, logo, avatar, startup screen, empty states, and running states. The core interface must remain a professional developer tool. Risk messages, approval dialogs, file changes, command execution, and errors must be objective and clear.

The current usage target is personal learning and self-use. This requirement does not claim that CodeCub is ready for public open-source or commercial release. Before any external release, the original project's license, copyright notices, attribution requirements, and redistribution rights must be confirmed. Existing license or copyright notices must not be removed. If the source project has no license, redistribution rights must not be assumed.

## 3. P0 And P1 Boundary

P0 is the first desktop version of CodeCub. P0 is a staged milestone set, not one commit, one pull request, or one single implementation task.

P0 must include:

- Full `pico` to `codecub` migration: package name, CLI command, default directory, README, documentation, tests, imports, and startup text.
- Desktop app under `desktop/`.
- Electron + Vite + React + TypeScript.
- Single-project session workflow.
- Machine-readable CLI app mode with JSONL events.
- Main chat interface plus run log sidebar.
- Chinese as the default UI language, with bilingual i18n architecture.
- OpenAI-compatible, Anthropic-compatible, and Ollama providers.
- Frontend-configurable model provider settings, including API credentials stored only through OS-backed secure storage.
- Default approval policy set to `ask`.
- Minimal diff preview.
- CodeCub-owned minimal session index.
- Recent projects plus manual folder selection.
- Streaming assistant text and real-time run status.
- Stop/cancel current run.
- Hybrid storage: global appData plus project-level `.codecub/`.
- Legacy `.pico/` detection and import.
- Windows packaging with an embedded Python backend executable.
- Full interactive terminal.
- Basic Git status.
- Near-release-level testing and acceptance.

P1 includes:

- Multi-project workspace.
- Local HTTP/WebSocket backend service.
- Fuller session and project management.
- Git diff and commit assistance.
- Richer pet personality states.
- Provider-native streaming and finer-grained cancellation/recovery.
- macOS and Linux packaging.

## 4. P0 Sub-Milestones

### P0.1 Backend App Protocol

Add an app-oriented backend mode such as `--app-mode`, `--json-events`, or an equivalent command. The backend must emit JSONL events through stdout. Electron sends user messages, approval decisions, and stop signals through stdin or IPC.

Electron must not rely on parsing normal terminal text as the primary integration method.

The backend input command protocol must also be machine-readable. P0 must define command messages for at least:

- Sending a user message.
- Approving an operation.
- Rejecting an operation.
- Canceling the current run.
- Closing the session or backend process.

Input commands must carry enough identifiers to match the related session, run, and approval request.

Minimum event types:

- `session_started`
- `user_message_received`
- `assistant_delta`
- `assistant_message`
- `tool_started`
- `approval_requested`
- `approval_resolved`
- `tool_result`
- `diff_summary`
- `shell_output`
- `run_canceled`
- `run_failed`
- `run_completed`

### P0.2 Brand, Package, And Data Directory Migration

Migrate `pico` to `codecub`.

Required migration targets:

- Python package directory.
- CLI entry point.
- `python -m codecub`.
- Default project data directory `.codecub/`.
- README and user-facing documentation.
- Tests and imports.
- Startup/welcome text.

Before this migration, the dirty worktree must be reviewed. Existing uncommitted changes must not be overwritten. If needed, the current changes must be saved, isolated, committed by the user, or explicitly approved before migration starts.

### P0.3 Electron Desktop Shell

Create the desktop app under `desktop/`.

Main process responsibilities:

- Select project folder.
- Start and stop the backend subprocess.
- Read JSONL backend events.
- Send user messages, approval decisions, and stop signals.
- Store appData settings and recent project metadata.

Renderer responsibilities:

- Chat UI.
- Run log sidebar.
- Settings UI.
- Approval dialog.
- Diff preview.
- Interactive terminal panel.

### P0.4 Approval, Logs, Diff, And Safety

Dangerous operations require approval by default.

Approval dialogs must show:

- Operation type.
- Target path or command.
- Current working directory.
- Timeout when applicable.
- Risk level.
- Diff or change summary when applicable.

The run log sidebar must support:

- Default user-friendly timeline mode.
- Debug mode with payloads, stdout/stderr, trace/report paths, error codes, and approval state.

Sensitive values must be redacted in both modes.

### P0.5 Packaging, Terminal, And Near-Release Acceptance

Windows packaging must include an embedded backend executable, such as `codecub-agent.exe` or an equivalent artifact.

The packaged Electron app must not depend on:

- The source repository path.
- A user-installed editable Python package.
- Manual installation of the Python project dependencies.

Development mode may still launch the backend from source for debugging.

P0.5 also includes:

- Full interactive terminal.
- Basic Git status.
- E2E tests.
- Windows packaging smoke test.
- Security and failure recovery tests.

## 5. Page And Panel Requirements

P0 must include these pages or panels:

- Welcome / Recent Projects page.
- Project Session page.
- Settings page.
- Approval dialog.
- Diff preview panel.
- Interactive terminal panel.
- `.pico` import prompt.
- Error and crash notification surface.

### Welcome / Recent Projects Page

Shows:

- CodeCub branding.
- Open project action.
- Recent project list.
- Basic empty state with restrained pet branding.

The app must not automatically scan the disk for projects.

### Project Session Page

Shows:

- Current project path.
- Git branch and dirty state.
- Provider/model state.
- Current session state.
- Main chat area.
- Run log sidebar.
- Input area with Send and Stop.

### Settings Page

Supports:

- Provider selection.
- Model name.
- Base URL or Ollama host.
- API key source.
- API key entry, update, clear, and masked status display.
- Optional provider connection test before or after saving.
- Approval policy.
- UI language.

P0 default language is Chinese.

Model API settings must be editable from the frontend settings page. The minimum supported fields are:

- Provider type: OpenAI-compatible, Anthropic-compatible, or Ollama.
- Model name.
- Base URL for OpenAI-compatible and Anthropic-compatible providers, or host URL for Ollama.
- API key for providers that require credentials.

The frontend must never display a full saved API key after it has been submitted. A saved credential may be shown only as a masked status such as "saved", "not configured", or a short non-sensitive suffix if the storage layer can provide it safely.

Saving model API settings must not require users to manually edit environment variables for the normal desktop flow. Environment variables may remain supported as a fallback or migration path, but they are no longer the only acceptable P0 path for using a real provider from the desktop app.

### Approval Dialog

Supports approval or rejection of risky agent operations.

The dialog must clearly present the command, path, diff, cwd, timeout, and risk summary when available.

### Diff Preview Panel

Shows minimal diff or change summary for `write_file` and `patch_file`.

P0 does not require hunk-level accept/reject.

### Interactive Terminal Panel

Provides a full interactive terminal with cwd set to the selected project directory.

Terminal output must not automatically enter the agent prompt unless the user explicitly references or sends it.

### `.pico` Import Prompt

If the selected project contains `.pico/`, CodeCub prompts the user to import legacy data into `.codecub/`.

CodeCub must not delete `.pico/` automatically.

## 6. JSONL Event Protocol

Every event must include:

- `type`
- `timestamp`
- `session_id`
- `run_id`

Tool-related events should include:

- Tool name.
- Arguments summary.
- Risk level.
- Approval state.
- Affected paths.
- Result status.

Shell-related events should include:

- Command.
- cwd.
- Timeout.
- Exit code when available.
- stdout/stderr chunks or summaries.

Diff-related events should include:

- Path.
- Operation type.
- Change summary.
- Minimal diff when available.

Error events must distinguish:

- Model request failure.
- Tool execution failure.
- Approval rejection.
- User cancellation.
- Backend crash.
- Shell timeout.
- Path escape.
- Legacy data import failure.

## 7. Streaming Requirements

The UI must support streaming assistant text through delta/token-like events.

The protocol must be streaming-first. If a provider does not support native streaming yet, the backend may split a completed response into `assistant_delta` events as a compatibility path. The UI and event contract must not degrade into only showing a final response.

P0 acceptance must verify the UI streaming display path.

## 8. Storage Requirements

Global appData stores:

- App settings.
- Recent projects.
- Session index.
- Window state.
- UI preferences.
- Provider configuration.

API keys, tokens, and other secrets must not be saved as plain text in appData. P0 implementation must use an OS-backed secure storage mechanism for API keys entered through the desktop settings page. Environment variables may be used only as a fallback or compatibility path, not as the only normal desktop configuration path.

Provider configuration saved in appData may include only non-secret fields and secret metadata:

- Provider type.
- Model name.
- Base URL or host URL.
- Whether a credential exists.
- The secure-storage service/account identifier needed to retrieve the credential.
- Optional non-sensitive display metadata, such as a masked suffix, if available.

Provider configuration saved in appData must not include:

- Full API keys.
- Access tokens.
- Refresh tokens.
- Passwords.
- Secret-shaped values copied from environment variables.

Clearing a provider credential from the frontend must remove the corresponding OS-backed secure-storage entry and update appData metadata so the UI no longer reports the credential as configured.

The minimal session index must include:

- Project path.
- Session id.
- Session title.
- Created time.
- Last used time.
- Provider and model.
- Last message or summary.

Project directory stores:

- New data under `.codecub/`.
- Sessions.
- Runs.
- Trace files.
- Reports.
- Memory data.

Project-level `.codecub/` data is local runtime state and should be ignored by Git by default, matching the existing `.pico/` behavior. If future requirements need shareable project configuration, that must be stored separately from private sessions, traces, reports, memory, and secrets.

Legacy data:

- Detect `.pico/`.
- Prompt before importing.
- Import failure must be recoverable.
- `.pico/` must not be modified or deleted automatically.

## 9. i18n Requirements

P0 defaults to Chinese UI.

UI text must be managed through an i18n structure. User-facing strings must not be scattered as hard-coded literals across components.

Chinese copy must be complete for P0. English copy may be minimal, but any visible English copy must still be coherent and user-facing. The UI must not show raw placeholder text or become incoherently mixed between Chinese and English.

Backend machine fields, event names, error codes, and test enum values may remain English.

## 10. Safety Requirements

Default approval policy is `ask`.

No approval required:

- File read.
- Search.
- Status inspection.

Approval required by default:

- File write.
- Patch.
- Shell command.

Agent file operations must remain restricted to the selected project directory.

Secrets must not appear in:

- UI.
- Run logs.
- Debug logs.
- Trace files.
- Reports.

Secret examples:

- API keys.
- Tokens.
- Passwords.
- Secret-shaped environment values.

Frontend model API configuration must preserve this safety boundary:

- API key input fields must use password-style rendering by default.
- Saved API keys must not be readable back into the renderer as plaintext.
- IPC responses must not return full secret values to the renderer.
- Backend launch code may pass a retrieved credential to the backend process environment or stdin only at run time.
- Logs and debug payloads must redact provider credentials and credential-like keys.

The interactive terminal is user-controlled. It does not bypass the agent approval model because it is not an agent tool call.

Stop/cancel must attempt to terminate:

- Current run.
- Active shell subprocess.
- Backend subprocess when necessary.

Canceled runs must be recorded as canceled, not as success or generic failure.

## 11. Basic Git Status

P0 only requires basic Git status:

- Current branch.
- Dirty or clean state.
- Changed file count.

P0 does not include:

- Stage/unstage.
- Commit.
- Push/pull.
- Branch management.
- Conflict resolution UI.

These may be handled manually through the terminal or deferred to P1.

## 12. Acceptance Requirements

P0 acceptance must cover:

- Python unit tests and regression tests.
- `codecub` CLI.
- `python -m codecub`.
- Package import paths.
- JSONL normal events.
- JSONL error events.
- Approval flow.
- Cancellation flow.
- Tool failure events.
- Electron main process project selection.
- Electron backend subprocess start/stop.
- Electron event parsing.
- React chat UI.
- React run log sidebar.
- React approval dialog.
- React settings page.
- React model API settings save/update/clear flow.
- OS-backed secure credential persistence for API keys entered through the frontend.
- React diff preview.
- React terminal panel.
- E2E open project flow.
- E2E send task flow.
- E2E streaming response display.
- E2E approve file write flow.
- E2E stop current task flow.
- E2E resume session flow.
- E2E view logs flow.
- Windows package install/start smoke test.
- Packaged app calls embedded backend.
- Packaged app exits and cleans up child processes.
- Path escape rejection.
- Sensitive information redaction.
- Verification that API keys are not persisted in appData, project `.codecub/`, traces, reports, run logs, or renderer-visible settings responses.
- Risky operation approval.
- Model failure recovery.
- Backend crash recovery.
- Shell timeout recovery.
- Approval cancellation.
- `.pico` import failure recovery.

## 13. Current Repository Constraints

The current repository has uncommitted changes.

Before implementation:

- Inspect the dirty worktree.
- Do not overwrite existing modifications.
- Identify files with user changes.
- Plan migration order and rollback strategy.

Before modifying or deleting an existing file, back up the original file under `E:\codex_backup` in a new timestamped folder with a short description.

Before creating new files, creating folders, installing dependencies, downloading tools/assets/models/datasets, or writing generated artifacts, obtain explicit user approval for the target path.

The approved requirements path for this document is:

```text
D:\代码备份\pico\pico-main\.codecub\spec
```

## 14. P0 Non-Goals

P0 does not include:

- Multi-project workspace.
- Complete Git panel.
- Hunk-level code review.
- Pet growth system.
- Affinity, feeding, skins, or pet economy.
- Automatic project scanning.
- macOS packaging.
- Linux packaging.
- Cloud sync.
- Account system.
- Plugin marketplace.
- Full MCP bridge.
- Full rewrite of the agent reasoning core.
- Public release license audit.
- Trademark search.
- Product name availability check.
