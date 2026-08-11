# CodeCub P0 Requirements

Date: 2026-06-11
Last updated: 2026-07-01
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
- Frontend-configurable model provider settings, including API credentials stored in the CodeCub executable directory global config folder.
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

Development packaging commands that rebuild `desktop/release/win-unpacked` must preserve any existing `desktop/release/win-unpacked/codecub-global` directory before invoking Electron Builder and restore it afterward. This prevents local API provider settings and API keys from reverting to stale appData fallback values after a rebuild. The preservation copy must be temporary, ignored by git, removed after restore, and must not be bundled into installer resources.

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

P0.9 should also include restrained dynamic effects. Motion must communicate state changes and interaction feedback, not serve as background decoration.

The project session screen must move toward a three-column workbench:

```text
Left: project context, chat history, plugins, skills
Center: chat, composer, terminal
Right: changes, run log, approvals
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

- Allowed: top toolbar, project sidebar surface, run inspector surface, inline activity stream, and small floating status surfaces.
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

The app must show a compact inline activity stream in the chat area that maps safe observable run states to visible status text. It must not display hidden chain-of-thought. Allowed status meanings include:

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

The inline activity stream may summarize the active run progression:

```text
Context -> Model -> Tool -> Diff -> Done
```

The activity stream is a visual summary only. It must derive from existing `run_status`, `tool_*`, `diff_summary`, and completion events. It must not require a new backend protocol in P0.9 unless a gap is found and explicitly documented.

Motion in P0.9 must remain minimal and purposeful. Allowed dynamic effects are:

- Assistant message arrival: subtle opacity and translate transition.
- Streaming state: compact inline activity indicator pulse or shimmer limited to the indicator.
- Activity stream update: active step transition and completed-step highlight.
- Side panel interaction: hover/focus elevation or soft background transition.
- Plugin/skill install success: brief highlight on the installed item.
- Approval attention: restrained warning emphasis without shaking or flashing.

Any animation must:

- Support reduced motion.
- Use transform and opacity rather than layout-changing properties.
- Be limited to state transitions such as activity stream updates, message arrival, panel expansion, or run progress changes.
- Not block user input.
- Stay short, generally 120-240ms for micro-interactions and no more than 360ms for view-level transitions.
- Avoid infinite animations except a very subtle active-run indicator.

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
- Excessive motion, particle effects, parallax backgrounds, or animations that make the coding workspace feel unstable.
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

Model API settings are global to the installed desktop app:

- Settings must be saved under a `codecub-global/` directory next to `CodeCub.exe`.
- Non-secret provider settings must be saved in `codecub-global/settings.json`.
- API keys must be saved in `codecub-global/secrets.json`.
- Opening a project must not cause model settings or API keys to be saved into that project.
- Project `.env` files must not override the desktop model provider, model, base URL, host, or API key.
- Existing project `.env` files must not be modified or deleted automatically.

OpenAI-compatible provider requests must handle common provider endpoint differences:

- The preferred request path is `/responses` when the provider supports it.
- If `/responses` returns an endpoint-not-supported status, currently HTTP `404`, `405`, or `501`, CodeCub must retry the same request intent through `/chat/completions`.
- The fallback must work for both non-streaming completion and provider-native streaming.
- Authentication, permission, quota, model-name, or validation failures must not be hidden by fallback. For example, HTTP `401`, `403`, `422`, and `429` must remain visible as model request failures.
- Chat-completions fallback payloads must use `messages`, `max_tokens`, and the configured `model`, `temperature`, base URL, and API key.
- The UI-facing error should remain actionable enough for users to distinguish bad credentials/base URL/model from an unsupported endpoint.

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

Global `codecub-global/` stores:

- App settings.
- UI preferences.
- Provider configuration.
- Provider API keys.

API keys entered through the desktop settings page are stored in `codecub-global/secrets.json` next to `CodeCub.exe`, per the current product requirement. This is local file storage and is less isolated than OS-backed credential storage; the UI and docs must make the storage target clear.

Provider configuration saved in `codecub-global/settings.json` may include only non-secret fields and secret metadata:

- Provider type.
- Model name.
- Base URL or host URL.
- Whether a credential exists.
- Optional non-sensitive display metadata, such as a masked suffix, if available.

Provider configuration saved in `codecub-global/settings.json` must not include:

- Full API keys.
- Access tokens.
- Refresh tokens.
- Passwords.
- Secret-shaped values copied from environment variables.

Clearing a provider credential from the frontend must remove the corresponding provider entry from `codecub-global/secrets.json` and update settings metadata so the UI no longer reports the credential as configured.

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

## 9.1 Appearance And Theme Requirements

P0 desktop must provide a visible glass-style appearance system instead of a subtle-only translucent treatment.

Required appearance modes:

- Dark glass mode: dark translucent surfaces with light text.
- Light glass mode: light translucent surfaces with dark text.

Users must be able to switch the appearance mode from the settings page.

Users must be able to choose the UI highlight color from safe preset colors. Presets must not default to red. A custom color input may be provided if it remains local and does not affect secrets or backend behavior.

The default P0 appearance is dark glass mode with a non-red highlight color.

Visual constraints:

- The desktop chrome must feel integrated with the app surface. The UI must not render as a rounded shell floating inside the native window.
- The top toolbar and internal workbench must be visually continuous, similar to a native media desktop app layout, rather than separated by a second outer container.
- On Windows, the app may hide the native title bar only if custom minimize, maximize/restore, close, and drag regions are provided.
- Main app shell, toolbar, side panels, cards, settings page, and workbench containers should use clearly visible translucent glass surfaces.
- Primary panels should use large rounded corners.
- Buttons, inputs, pills, side list items, and message containers should use rounded corners consistent with the glass theme.
- Text contrast and code readability must remain higher priority than transparency.
- Terminal, diff, error, approval, and log content must remain readable in both dark and light modes.
- Motion must remain reduced-motion safe.
- The product must still read as a CodeCub code agent desktop tool, not as a game dashboard.
- GSAP may be used for scoped React motion when it improves polish. Animations must use cleanup-safe React patterns, prefer transform/opacity, and respect `prefers-reduced-motion`.
- The project workbench must keep a clear visual hierarchy: compact project context on the left, chat/work area as the primary center, and run state/changes/logs/approvals organized on the right.
- The terminal must not dominate the default workbench layout. It should be available as a bottom drawer or similarly compact panel and expand only when needed.
- The terminal drawer must visually belong to the app theme. It should not render as a raw gray header plus unstyled black rectangle. Terminal colors, header, typography, and collapsed state must be intentionally styled.
- Empty chat state should communicate the primary action and keep the center workbench visually focused.
- The run inspector should avoid vertical pile-up by grouping secondary views such as changes, logs, and approvals.

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
- Backend launch code may pass a retrieved credential from `codecub-global/secrets.json` to the backend process environment only at run time.
- Backend launch code must clear inherited provider environment variables before injecting the selected global provider credential.
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

## 11.10 P0.10 UI Readability and User-Controlled Chat History

The desktop app must keep the glass visual direction, but dense work areas must remain readable:

- Main chat, sidebar lists, inspector panels, composer, and terminal must not stack multiple blur filters over text.
- Text contrast must be strong enough in both dark and light themes.
- Mojibake or corrupted UI copy is not acceptable in shipped Chinese or English strings.
- Window control symbols must render as clear controls, not corrupted characters.

Project navigation must include:

- A visible Home button from the project session page back to the welcome page.
- The existing settings navigation must remain available.

Project chat history must be user-controllable:

- Users can create a new empty chat from the project sidebar.
- Users can delete an existing chat from the project sidebar after explicit confirmation.
- Chat history list titles should use a human-readable title, preferably the first user message.
- Raw session ids must not be the primary visible title.
- Empty sessions should not appear unless they were explicitly created by the desktop UI.
- Corrupt or malformed session files must continue to be ignored.

Deleting a chat removes only the matching `.codecub/sessions/<session-id>.json` file after user confirmation.

## 11.11 P0.11 Readable Run Log and Simplified Inspector

The run inspector must be readable at the default right-column width:

- The top status area must avoid stacking several heavy bordered controls.
- CodeCub status, run trail, and inspector tabs should be visually lightweight and scannable.
- The run trail should communicate progress without looking like a second button toolbar.

The run log must be understandable without debug mode:

- Default log entries must show a translated title, detail, and return/result summary.
- Raw backend JSON must not be shown in the default log view.
- High-volume streaming delta events should be hidden from the default readable log.
- Debug mode may show extra fields and raw sanitized JSON, but raw JSON must be placed behind an expandable section.
- Paths and internal ids should be shortened in the readable view and reserved for debug details.

## 11.12 P0.12 Inspector Readability Polish

The run inspector must avoid cramped or unreadable controls:

- The idle/ready status area should use a compact two-line layout instead of a large pill plus bullet-like progress row.
- The run trail should be readable as lightweight progress text, not as missing-label buttons or cluttered bullets.
- Debug raw-event disclosure controls must show visible text in both light and dark themes.
- Tool status details should use stable ASCII separators to avoid mojibake.

P0.12 is superseded by P0.13 for the right inspector status area: the right inspector must no longer show the CodeCub ready chip or the Context/Model/Tool/Diff/Done run trail.

## 11.13 P0.13 Inline Chat Activity Stream

The run progress experience must move into the center conversation area, similar to Codex Desktop:

- The chat must show a compact elapsed-time summary such as `已处理 12s` for the current or most recent run.
- The chat must show readable activity entries for observable work, such as receiving a task, running a tool, checking file changes, waiting for approval, receiving model output, completion, failure, or cancellation.
- The activity stream must use translated, user-facing text by default. It must not show raw backend JSON in the conversation.
- High-volume `assistant_delta` events must not render as one row per token; they may update a single "receiving model response" state.
- Tool activity may show the tool name and a compact command or output excerpt when available.
- Diff activity must summarize the number of changed files when available.
- The right inspector must keep only the `变更 / 日志 / 审批` tabs and the selected tab content.
- The right inspector top status chip and `上下文 / 模型 / 工具 / 变更 / 完成` row must be removed.
- The activity stream must be derived from existing backend events in P0.13. A backend protocol expansion may be planned later if more precise "tool started" or "file editing started" timing is required.

## 11.14 P0.14 Product Polish and Command Deck UI

The desktop frontend must feel like a coherent, product-grade app rather than a functional prototype.

The visual direction is `CodeCub Command Deck`:

- The home page must be a complete launcher, not a sparse two-column placeholder.
- The home page must give the brand/product a clear first-viewport presence while still making project opening and recent projects the primary workflow.
- Recent project items must look intentionally clickable and structured, with name, path, and a compact launch affordance.
- The project page must read as one integrated coding workbench, not several unrelated bordered boxes.
- The top toolbar, side rail, central chat/composer/terminal, and right inspector must use consistent spacing, radius, border, shadow, and hierarchy.
- The project sidebar must present project identity, Git state, chat history, and extensions with clear grouping and compact density.
- The center empty chat state must look intentional and guide the user toward starting work without looking like a landing-page hero.
- The terminal must read as a docked developer surface and must not visually dominate when collapsed.
- The run inspector must remain focused on `变更 / 日志 / 审批` and readable at the default width.
- Window controls must not show mojibake.
- Light theme text must remain crisp; translucent surfaces must not blur dense text.
- Motion must be limited to load, hover, message, panel, and terminal state transitions, and must respect reduced motion.
- P0.14 must not change backend contracts, model provider behavior, storage format, approval behavior, or terminal IPC behavior.

## 11.15 P0.15 Launcher, Empty Chat, and CLI Polish

P0.15 closes the remaining product-quality gaps in the launcher, empty chat workflow, backend startup path, and terminal CLI.

Launcher requirements:

- If recent projects exist, the home page must provide a primary one-click action to continue the latest project.
- The one-click continue action must enter the project page through the same backend startup path as opening a recent project.
- The normal open-project picker must remain available as a secondary action.
- Home-page recent project cards must be dense enough to avoid large empty placeholder areas.

Empty chat requirements:

- Creating a chat while there are no messages must create a visible, selected chat-history row.
- Empty user-created chats must show a localized `New chat` title instead of looking identical to no chat.
- The center chat area must show a clear "new chat ready" state when an empty manually created chat is active.
- Empty non-user-created sessions must remain hidden.

Terminal CLI requirements:

- Running `codecub` in a terminal must present a polished interactive agent shell instead of a bare prompt.
- The embedded terminal must accept keyboard input after opening, focus the xterm surface when clicked, and keep the active prompt visible instead of clipping it below the drawer.
- The embedded terminal must provide a visible `Run CodeCub` action that sends `codecub` to the terminal for users who do not want to type the command manually.
- The interactive CLI prompt must be Windows-console safe ASCII text.
- The terminal shell must show observable activity states such as context building, model response, tool execution, file change summary, completion, failure, or cancellation when those events occur.
- The terminal shell must not expose hidden chain-of-thought.
- App-mode JSONL output used by the desktop must remain machine-readable and unchanged.

## 11.16 P0.16 Independent Scroll, Activity Collapse, and Long-Run Visibility

P0.16 fixes workbench usability problems found during long-running repository tasks.

Workbench scrolling requirements:

- The desktop window itself must not become the primary scroll container in project mode.
- The left project sidebar, center conversation message list, and right run inspector must each own their own vertical scrolling.
- The center composer and terminal dock must remain visible while the message list scrolls.
- The right run log scrollbar must belong to the right inspector area, not the outer window edge.

Inline activity requirements:

- The inline activity summary must be an actual expand/collapse button.
- The collapsed summary must still show elapsed time and the current observable phase.
- Activity details must hide when collapsed and return when expanded.
- The activity stream must never expose hidden chain-of-thought.

Long-run visibility requirements:

- App-mode backend runs must emit periodic `run_status` heartbeat events while a run is active and no newer phase event has appeared.
- Heartbeat status events must preserve the current phase/label where possible and include enough information for the frontend to show that the agent is still working.
- The center conversation must auto-scroll to the latest message or activity update while a task is running.
- A long model request, long tool run, or non-streaming provider response must not leave the UI looking idle for many minutes.

Run-limit requirements:

- Backend default `max_steps` must be `80`, not the earlier low development default.
- The fixed step limit is a safety fallback, not the primary mechanism for stopping normal code-analysis tasks.
- Repetition detection must be scoped to the current user request, not previous chat turns.
- A no-progress repeat is initially defined as the same tool name with the same arguments repeated consecutively.
- CodeCub must stop the current run when the same no-progress tool action reaches `5` consecutive attempts.
- The stop reason for this case must be persisted as `repeated_no_progress` in run artifacts.

## 11.17 P0.17 Encoding-Safe Desktop Protocol

P0.17 fixes Windows packaged-app encoding failures where Chinese paths or Chinese chat messages could appear as mojibake in the chat list, message bubbles, run log, or session files.

Encoding requirements:

- Electron-to-backend launch arguments must not pass non-ASCII project paths as raw Windows argv text in packaged mode.
- Project paths passed to the backend must support UTF-8 Chinese and other Unicode characters.
- Electron-to-backend stdin commands must be ASCII-safe JSONL so user messages are not decoded through the Windows ANSI code page.
- Backend-to-Electron stdout events must be ASCII-safe JSONL so renderer parsing receives valid Unicode after JSON decoding.
- Terminal `codecub` bootstrap must pass the current working directory in an encoding-safe way.
- Existing corrupted session text must not be shown as raw mojibake in the chat history list or loaded message bubbles. If it cannot be safely reconstructed, the UI must show a clear recoverability notice instead.

P0 acceptance must verify:

- A packaged backend started from a Chinese project path emits a correct `session_started.payload.cwd` after JSON parsing.
- A Chinese user message sent from the desktop remains Chinese in `user_message_received` and persisted chat history.
- Raw backend stdout and stdin transport can remain ASCII while JSON parsing recovers the original Unicode strings.
- Chat history summaries and loaded messages do not display mojibake strings such as `浣犵...`, `鏌ョ...`, `锟`, or replacement characters.

## 11.18 P0.18 Context-Build Stall Recovery and Readable Long-Run Status

P0.18 fixes the case where a desktop task can stay on "building context" for many minutes without reaching the model request stage or showing a useful reason in the UI.

Backend context-build requirements:

- Workspace Git metadata collection must never block an agent run indefinitely.
- If the selected project is not a Git workspace, CodeCub must skip Git subprocess calls and continue with a safe fallback workspace snapshot.
- If a Git subprocess times out or fails, CodeCub must return fallback Git metadata and continue the run.
- Context construction must emit observable phase events before potentially slow steps, including repository-state checking, memory loading, and prompt construction.
- Run traces must contain enough context-step events to distinguish "stuck before model request" from "model is thinking".

Frontend long-run readability requirements:

- Default run logs must not show repeated heartbeat cards as ordinary user-facing events.
- Consecutive duplicate run-status events should be collapsed in the readable log.
- Debug mode may still show raw heartbeat payloads for diagnosis.
- Inline chat activity must show the current translated phase and elapsed time.
- If heartbeat events indicate no newer backend step for a prolonged period, the inline activity stream must show a clear "no new progress" hint instead of looking normally active.
- The UI must not expose hidden chain-of-thought while reporting these observable phases.

P0 acceptance must verify:

- A non-Git project does not call Git while building workspace context.
- A timed-out Git metadata command is killed or abandoned and returns fallback metadata.
- A run trace includes context-step events before `prompt_built` and `model_requested`.
- The readable run log hides heartbeat noise by default while debug mode can still inspect raw events.
- The inline activity stream displays a stalled heartbeat as a translated, user-readable status.

## 11.19 P0.19 Market-Level Frontend Polish

P0.19 is a visual and usability consolidation pass for the desktop frontend. It does not add new backend protocol behavior. Its goal is to remove the accumulated patchwork feeling from earlier UI iterations and make the home page and project workbench feel like one finished desktop product.

Design requirements:

- The app must avoid a visible "shell inside a shell" layout. The title bar, home page, workbench columns, chat canvas, terminal dock, and inspector should read as one integrated desktop surface.
- The home page must not leave a large empty gray slab when recent projects are missing or few. Empty state and recent project rows must have intentional density, spacing, and clear action affordance.
- The project workbench must keep left context, center chat, and right inspector visually distinct without making every section compete as a heavy card.
- Chat messages, inline activity status, composer, and terminal dock must align to readable center measures so the center column does not look scattered.
- The terminal must look like a deliberate developer dock, with readable contrast, compact controls, and a stable expanded height.
- The run inspector must be simplified: tabs are compact, log cards are readable, debug details are secondary, and ordinary users should not see raw JSON or mojibake in default mode.
- Light and dark themes must preserve crisp text on translucent surfaces. Blur, shadows, and opacity must not reduce readability.
- Motion may be used for polish but must respect reduced-motion settings and must not make the interface feel unstable.

P0.19 acceptance must verify:

- A dedicated CSS layer documents the market-polish pass and centralizes the final surface, spacing, message-width, and dock-width tokens.
- The home page, project workbench, chat activity stream, terminal panel, inspector tabs, and run log cards are covered by regression tests or token guards.
- Screenshot review covers at least the home page and the project workbench after implementation.
- No new behavior writes outside the selected project workspace, and no UI polish change weakens existing terminal, history, plugin, model settings, or backend-start flows.

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
- React GSAP-enhanced workbench motion that respects reduced motion.
- React terminal drawer collapsed by default.
- React terminal dock visual polish and xterm color theme.
- React readable glass surfaces without blurred text in dense work areas.
- React Home navigation from project session page.
- React user-created chat sessions.
- React chat session deletion with confirmation.
- React chat history filtering that hides empty non-user-created sessions.
- React mojibake-free Chinese and English UI copy.
- React simplified run inspector header.
- React translated readable run log cards.
- React run log debug mode with expandable raw event payloads.
- React run log default view hiding raw JSON and assistant delta noise.
- React visible debug raw-event disclosure controls.
- React compact inspector ready-state layout.
- React stable ASCII separators in readable log details.
- React inline chat activity stream with elapsed time.
- React translated activity rows for tool runs, file changes, model streaming, completion, failure, and approval states.
- React run inspector without the old CodeCub ready chip and Context/Model/Tool/Diff/Done row.
- React product-grade Command Deck home page.
- React integrated project workbench visual polish.
- React P0.19 market-polish visual layer for cohesive home, workbench, chat, terminal, and log surfaces.
- React screenshot-reviewed dense layouts with no obvious empty slabs, nested-shell look, or competing card clutter.
- React mojibake-free toolbar window controls.
- React crisp light theme text on translucent surfaces.
- React one-click continue-latest project launcher.
- React visible selected empty user-created chat.
- Terminal `codecub` interactive shell with observable activity output.
- Embedded terminal focus, prompt visibility, and one-click `Run CodeCub` behavior.
- React activity stream expand/collapse behavior with current phase visible while collapsed.
- React independent left, center, and right scroll containers in project mode.
- App-mode heartbeat `run_status` events during long-running tasks.
- Backend Git context collection timeout/fallback behavior.
- Backend context-step trace events before prompt/model request.
- React readable run log hiding heartbeat noise by default.
- React inline activity stalled-heartbeat warning.
- React auto-scroll to the latest conversation activity while a run is active.
- Encoding-safe desktop/backend protocol for Chinese paths and Chinese chat messages.
- React chat history and message rendering without raw legacy mojibake.
- React focused empty chat state.
- React run inspector tab grouping for changes, logs, and approvals.
- React approval dialog.
- React settings page.
- React appearance settings save/update flow.
- React model API settings save/update/clear flow.
- React dark glass and light glass theme rendering.
- React highlight color selection and persistence.
- Executable-directory `codecub-global/secrets.json` credential persistence for API keys entered through the frontend.
- React diff preview.
- React terminal panel.
- E2E open project flow.
- E2E send task flow.
- E2E streaming response display.
- E2E inline activity stream display.
- E2E approve file write flow.
- E2E stop current task flow.
- E2E resume session flow.
- E2E local skill install flow.
- E2E local plugin install flow.
- E2E UI layout smoke for project session page.
- E2E right inspector tabs without status chip or run trail.
- E2E view logs flow.
- Windows package install/start smoke test.
- Packaged app calls embedded backend.
- Packaged app exits and cleans up child processes.
- Path escape rejection.
- Sensitive information redaction.
- Verification that API keys are not persisted in appData, project `.codecub/`, project `.env`, traces, reports, run logs, or renderer-visible settings responses.
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
