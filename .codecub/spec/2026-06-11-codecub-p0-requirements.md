# CodeCub P0 Requirements

Date: 2026-06-11
Last updated: 2026-06-16
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
- Provider-native streaming assistant text for the OpenAI-compatible provider path used by Qwen testing, plus real-time run status.
- Project-scoped chat history that can list and resume previous `.codecub/sessions` records from the desktop app.
- Project-scoped local plugin and skill installation from user-selected local folders.
- P0.9 UI polish pass: three-column desktop workbench, unified visual tokens, clearer chat hierarchy, CodeCub status chip, and run trail.
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
- Plugin marketplace, remote plugin discovery, extension updates, and extension runtime execution.
- Git diff and commit assistance.
- Richer pet personality states.
- Provider-native streaming parity for Ollama and Anthropic-compatible providers.
- Finer-grained cancellation/recovery.
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
- `run_status`
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

### P0.8 Project Chat History, Plugins, And Skills

Project chat history must be visible from inside the selected project session page.

The desktop app must read existing project session files from:

```text
<project>/.codecub/sessions/*.json
```

For each session, the UI must show at least:

- Session id.
- Created time when available.
- Last used or file modified time.
- Message count.
- Last user or assistant message preview when available.

Clicking a chat history item must resume that session through the existing backend `--resume <session_id>` flow. The UI should load the saved user and assistant messages before or during resume so that reopening a project does not look like a blank new chat.

Session history must remain project-scoped. The desktop app must not scan arbitrary disks for sessions, must not merge sessions across projects, and must not write chat history outside the selected project's `.codecub/` directory.

P0.8 also adds a local project-level extension manager for plugins and skills.

The extension manager must:

- List installed skills from `<project>/.codecub/skills`.
- List installed plugins from `<project>/.codecub/plugins`.
- Install a skill by asking the user to choose a local folder that contains `SKILL.md`.
- Install a plugin by asking the user to choose a local folder that contains `plugin.json`.
- Copy the selected folder into the selected project's `.codecub/skills/<id>` or `.codecub/plugins/<id>` directory.
- Reject an install with a clear error if the selected folder is missing its manifest or the destination id already exists.

The extension manager must not:

- Download extensions from a remote marketplace.
- Execute plugin code as part of installation.
- Write outside the selected project `.codecub/` directory.
- Overwrite an existing installed extension in P0.8.

The minimum UI surface is a project-side panel or equivalent project-scoped view showing installed skills/plugins, install actions, and install errors.

### P0.9 Desktop UI Polish

P0.9 improves the desktop interface aesthetics and information architecture without changing the agent core behavior, backend event contract, model provider behavior, storage format, or packaging requirements.

The design direction is `Quiet Cub Workbench`: a professional desktop code agent interface with restrained CodeCub pet identity. The product should read primarily as a serious developer tool, not as a decorative toy app.

P0.9 should include a restrained translucent feel. This means selected surfaces may use glass-like semi-transparent backgrounds, light backdrop blur, and subtle borders. Transparency must support readability rather than decoration.

The project session screen must move toward a three-column workbench:

```text
Left: project context, chat history, plugins, skills
Center: chat, composer, terminal
Right: run status, approvals, diff preview, run log
```

The UI must use a tokenized visual system for:

- Background, surface, raised surface, border, muted text, primary text.
- Brand primary, brand accent, success, warning, danger, and code surface.
- Font sizes and weights for page title, panel title, body, metadata, and code/log text.
- Border radius, spacing, and focus ring values.

The recommended P0.9 palette is:

```text
Background: #F6F8FA
Surface: #FFFFFF
Subtle surface: #EEF2F5
Text: #17202A
Muted text: #64717F
Border: #D9E0E7
Brand primary: #2F6F73
Brand accent: #D6A84F
Danger: #B42318
Success: #1F7A4D
Warning/running: #9A5A00
Code surface: #111820
Glass surface: rgba(255, 255, 255, 0.72)
Glass border: rgba(217, 224, 231, 0.72)
```

Transparency rules:

- Allowed: top toolbar, project sidebar surface, run inspector surface, status chip, run trail, and small floating status surfaces.
- Avoid: chat message body text surfaces, terminal surface, approval risk content, diff/code blocks, and error banners when transparency would reduce contrast.
- Text contrast must remain readable against both normal and translucent surfaces.
- Backdrop blur must be subtle and must have a non-blur fallback through a readable background color.
- The UI must not use decorative glass orbs, large blurred background blobs, or visual effects that make the code/tooling workspace feel noisy.

The chat area must distinguish:

- User messages.
- Assistant messages.
- Streaming assistant state.
- Empty state.
- Tool/process status, which should not visually compete with final assistant text.

The app must add a compact CodeCub status chip that maps safe observable run states to visible status text. It must not display hidden chain-of-thought. Allowed status meanings include:

- Ready.
- Analyzing project context.
- Requesting model response.
- Receiving model response.
- Reading files.
- Preparing changes.
- Running command.
- Waiting for approval.
- Completed.
- Failed or needs attention.

The right-side run area must add a compact run trail showing the active run progression:

```text
Context -> Model -> Tool -> Diff -> Done
```

The run trail is a visual summary only. It must derive from existing `run_status`, `tool_*`, `diff_summary`, and completion events. It must not require a new backend protocol in P0.9 unless a gap is found and explicitly documented.

Motion is optional in P0.9 and must be minimal. Any animation must:

- Support reduced motion.
- Use transform and opacity rather than layout-changing properties.
- Be limited to state transitions such as status chip updates, message arrival, panel expansion, or run trail step changes.
- Not block user input.

P0.9 must preserve the current functional scope:

- Opening a project.
- Recent projects.
- Project chat history and resume.
- Local plugin/skill installation.
- Model API settings.
- Approval flow.
- Diff preview.
- Run log.
- Interactive terminal.
- Legacy `.pico` import prompt.

P0.9 must not introduce:

- A full pet growth/economy system.
- Plugin marketplace.
- New backend service architecture.
- New model provider behavior.
- Large decorative background animations.
- Decorative glassmorphism effects that reduce readability.
- Hidden reasoning display.

## 5. Page And Panel Requirements

P0 must include these pages or panels:

- Welcome / Recent Projects page.
- Project Session page.
- Settings page.
- Approval dialog.
- Diff preview panel.
- Interactive terminal panel.
- Project chat history panel.
- Project plugin and skill manager panel.
- CodeCub status chip.
- Run trail panel.
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
- Project chat history entry point.
- Project plugin and skill manager entry point.
- CodeCub status chip for observable run state.
- Run trail for high-level execution progress.
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

P0 streaming target is a Codex-like app experience: the assistant answer must appear progressively, and the UI must show how long the run has been active and what the agent is currently doing.

P0 must implement true provider-native streaming for the OpenAI-compatible provider path used by Qwen testing. The backend must request a streaming response from the provider when supported, read text chunks incrementally, and emit `assistant_delta` events while the provider response is still in progress. It is not sufficient for this P0 feature to wait for the full answer and then emit one whole-answer delta.

The frontend must render `assistant_delta` incrementally in the active assistant message. The final `assistant_message` event remains the durable complete message used for final state, history, and run completion. The frontend must avoid duplicating final text when both delta and final events are received.

Providers that are not part of the P0 native-streaming target may keep the compatibility behavior temporarily: they may emit one or more `assistant_delta` events after receiving a completed response. This fallback must not change the frontend contract.

The app must not expose hidden chain-of-thought or private reasoning. "Thinking" UI means observable process status only, such as:

- Building context.
- Requesting model response.
- Receiving model response.
- Executing a named tool.
- Waiting for user approval.
- Applying or summarizing file changes.
- Completed, canceled, or failed.

The backend should emit a `run_status` event whenever the observable state changes. A `run_status` payload should include:

- `phase`: stable machine-readable status such as `building_context`, `model_request`, `model_streaming`, `tool_running`, `waiting_approval`, `finalizing`, `completed`, `failed`, or `canceled`.
- `label`: short user-facing status text.
- `detail`: optional safe detail, such as tool name, path summary, or provider name.
- `started_at`: run start timestamp when available.
- `elapsed_ms`: elapsed run time when available.

The frontend must show a visible status area for the active run. It must include elapsed time, update while the run is active, and display the latest safe status label. The status must remain understandable even when no assistant text has arrived yet.

P0 acceptance must verify:

- OpenAI-compatible/Qwen streaming emits multiple `assistant_delta` events before the final `assistant_message` for a non-trivial response.
- The chat view displays partial assistant text before final completion.
- Final text is not duplicated after `assistant_message`.
- The active run status area shows elapsed time while running.
- The active run status changes for at least model request/model streaming and tool or approval phases when those phases occur.
- Hidden model reasoning is not displayed in the UI, trace, run log, or persisted session data.

## 7.1 Project Chat History Requirements

Project chat history is a desktop UI over the existing project `.codecub/sessions` storage.

The app must support:

- Listing sessions for the currently selected project.
- Showing a compact summary for each session.
- Loading persisted user and assistant messages for a selected session.
- Starting the backend with `--resume <session_id>` when a listed session is selected.
- Refreshing the list after starting a new backend session or after backend startup fails.

The app must tolerate malformed or partially written session files. A bad session file must not prevent other sessions from being listed; it may appear as a skipped record or produce a non-fatal UI error.

The app must not display tool-only internal history as normal chat bubbles. User-visible chat restore should include user and assistant messages only.

## 7.2 Plugin And Skill Installation Requirements

P0.8 plugins and skills are local project assets, not a marketplace feature.

An installed skill is a directory under:

```text
<project>/.codecub/skills/<skill_id>
```

The source directory must contain `SKILL.md`.

An installed plugin is a directory under:

```text
<project>/.codecub/plugins/<plugin_id>
```

The source directory must contain `plugin.json`.

The installer must use the selected folder name as the initial id, normalized to a filesystem-safe id. If the normalized id is empty or the destination already exists, installation must fail with a clear error. P0.8 does not require rename, uninstall, update, remote registry, dependency resolution, or plugin runtime activation.

Extension listing must show at least:

- Id.
- Type: skill or plugin.
- Display name when available from the manifest.
- Source path if recorded.
- Installed time when available.

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

Project chat history reads durable session data from project `.codecub/sessions`. The global session index may cache lightweight summaries later, but P0.8 must not require that cache to be correct before a user can resume a project session.

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
- React project chat history list and resume flow.
- React project plugin and skill manager.
- React three-column project workbench layout.
- React CodeCub status chip.
- React run trail.
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
- E2E local skill install flow.
- E2E local plugin install flow.
- E2E UI layout smoke for project session page.
- E2E run status chip and run trail display.
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
- Remote plugin or skill download.
- Plugin runtime execution.
- Extension overwrite/update/uninstall.
- Full MCP bridge.
- Full rewrite of the agent reasoning core.
- Public release license audit.
- Trademark search.
- Product name availability check.
