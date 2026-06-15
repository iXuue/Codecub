# CodeCub P0 Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the CodeCub P0 desktop code agent baseline from the current Pico CLI project while preserving existing work, moving toward a branded Windows desktop app with a machine-readable Python backend.

**Architecture:** P0 is implemented as staged deliverables. The Python backend first gains an app-mode JSONL protocol, then the project is migrated from `pico` to `codecub`, then the Electron desktop app integrates with the backend subprocess, approval flow, logs, diff preview, terminal, packaging, and acceptance tests.

**Tech Stack:** Python 3.10+, pytest, Electron, Vite, React, TypeScript, JSONL subprocess protocol, Windows packaging, embedded Python backend executable.

---

## 0. Plan Scope And Execution Rules

This is the master implementation plan for `.codecub/spec/2026-06-11-codecub-p0-requirements.md`.

P0 is too large for one coding pass. Execute it as staged subplans:

1. P0.0 repository protection and baseline.
2. P0.1 backend app protocol.
3. P0.2 `pico` to `codecub` migration.
4. P0.3 Electron desktop shell.
5. P0.4 approval, logs, diff, safety, and storage.
6. P0.5 terminal, Git status, packaging, and near-release acceptance.

Each stage must finish with tests and a written status note before the next stage starts.

This master plan is not an execution-level coding plan for the whole P0 scope. Before implementing any P0 stage, create the corresponding stage plan listed in Task 1, review it against the requirements, and get explicit user approval. A stage may not begin implementation directly from this master plan.

Current stage status as of 2026-06-15 15:24 Asia/Shanghai:

- P0.1 backend app protocol: completed and verified in `.codecub/plan/2026-06-11-codecub-p0-1-backend-app-protocol-plan.md`.
- P0.2 package migration: completed and verified in `.codecub/plan/2026-06-11-codecub-p0-2-package-migration-plan.md`.
- P0.3 Electron desktop shell: completed for code/build/backend smoke verification in `.codecub/plan/2026-06-11-codecub-p0-3-desktop-shell-plan.md`.
- P0.3 remaining validation gap: no full visual/browser screenshot validation was completed because no Browser control tool was available in that turn.
- P0.4 approval, logs, diff, safety, and storage: completed and verified in `.codecub/plan/2026-06-11-codecub-p0-4-approval-diff-storage-safety-plan.md`.
- P0.5 terminal, Git status, packaging, and near-release acceptance: completed and verified in `.codecub/plan/2026-06-11-codecub-p0-5-packaging-terminal-acceptance-plan.md`.
- P0.6 release hardening: completed and verified in `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`.
- P0 acceptance checklist: recorded in `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`.

Stage plans must replace any illustrative or comment-only test sketches in this master plan with complete test code, exact files, exact commands, and expected failure/pass output. If the stage plan changes a P0 requirement or narrows scope, update the requirements document in the same change set.

Requirements maintenance rule:

- If a requirement changes, update `.codecub/spec/2026-06-11-codecub-p0-requirements.md` in the same change set.
- If a plan changes, update this file in the same change set.
- Do not rely on chat-only requirement changes.

Filesystem safety rule:

- Before modifying an existing file, back it up under `E:\codex_backup\<timestamp>-<short-description>\`.
- Preserve enough path information in the backup to restore the original file.
- Before deleting any file or folder, ask for explicit confirmation.
- Before installing dependencies, downloading tools, or creating generated artifacts, ask for explicit confirmation.

Dirty worktree rule:

- The current repository already has uncommitted changes in:
  - `pico/cli.py`
  - `pico/context_manager.py`
  - `pico/memory.py`
  - `pico/runtime.py`
  - `tests/test_context_manager.py`
  - `tests/test_memory.py`
  - `tests/test_pico.py`
  - `agent.md`
  - `.codecub/`
- Do not overwrite these changes without reading and accounting for them.

Path documentation rule:

- Prefer repository-relative paths inside plans and implementation notes.
- Use absolute paths only when they are required for a user-approved location such as backups or the current workspace root.
- If a terminal renders a Chinese path incorrectly, treat it as an output encoding issue and do not copy the mojibake text into new documentation.

## 1. Target File Structure

### Python Backend After P0.2

Expected final backend package:

```text
codecub/
  __init__.py
  __main__.py
  app_protocol.py
  app_runner.py
  cli.py
  context_manager.py
  evaluator.py
  memory.py
  metrics.py
  models.py
  run_store.py
  runtime.py
  task_state.py
  tools.py
  workspace.py
```

Responsibilities:

- `codecub/app_protocol.py`: typed event and command schema helpers for JSONL app mode.
- `codecub/app_runner.py`: stdin/stdout app-mode loop that adapts Electron commands to the runtime.
- `codecub/cli.py`: human CLI and app-mode CLI entry selection.
- `codecub/runtime.py`: agent control loop, tool execution, approvals, run state, trace/report generation.
- `codecub/tools.py`: safe file/search/shell/write/patch/delegate tool registry.
- `codecub/models.py`: provider clients.
- `codecub/run_store.py`: `.codecub/runs/` artifact persistence.
- `codecub/memory.py`: working and durable memory.
- `codecub/context_manager.py`: prompt assembly and reduction.
- `codecub/workspace.py`: workspace discovery, Git metadata, path safety.

### Desktop App

Expected desktop structure:

```text
desktop/
  package.json
  tsconfig.json
  vite.config.ts
  electron/
    main.ts
    preload.ts
    backendProcess.ts
    appConfig.ts
    secureSecrets.ts
    projectStore.ts
    terminal.ts
    gitStatus.ts
  src/
    main.tsx
    App.tsx
    i18n/
      zh-CN.ts
      en-US.ts
      index.ts
    state/
      appStore.ts
      backendEvents.ts
      sessionIndex.ts
    components/
      WelcomePage.tsx
      ProjectSessionPage.tsx
      ChatView.tsx
      RunLogSidebar.tsx
      ApprovalDialog.tsx
      DiffPreviewPanel.tsx
      SettingsPage.tsx
      TerminalPanel.tsx
      GitStatusBadge.tsx
      ErrorNotice.tsx
    styles/
      app.css
  tests/
    backendEvents.test.ts
    sessionIndex.test.ts
    appConfig.test.ts
```

Responsibilities:

- `desktop/electron/main.ts`: Electron lifecycle, windows, IPC registration.
- `desktop/electron/backendProcess.ts`: spawn/stop backend executable or source backend in dev mode.
- `desktop/electron/appConfig.ts`: appData-backed settings, recent projects, window state.
- `desktop/electron/secureSecrets.ts`: OS secure storage or explicit environment variable strategy.
- `desktop/electron/projectStore.ts`: recent projects and session index persistence.
- `desktop/electron/terminal.ts`: interactive terminal bridge.
- `desktop/electron/gitStatus.ts`: branch, dirty state, changed file count.
- `desktop/src/state/backendEvents.ts`: JSONL event parser and reducer.
- `desktop/src/state/sessionIndex.ts`: minimal session index model.
- `desktop/src/i18n/*`: Chinese-first bilingual text structure.

### Project Data

Trackable project documents:

```text
.codecub/spec/
.codecub/plan/
```

Runtime data to ignore by Git:

```text
.codecub/sessions/
.codecub/runs/
.codecub/memory/
.codecub/cache/
.codecub/logs/
```

Do not ignore the whole `.codecub/` directory, because `.codecub/spec/` and `.codecub/plan/` are project planning artifacts.

## 2. Implementation Tasks

### Task 1: Repository Protection Baseline

**Files:**

- Read: `D:\代码备份\pico\pico-main\.codecub\spec\2026-06-11-codecub-p0-requirements.md`
- Read: `D:\代码备份\pico\pico-main\pyproject.toml`
- Read: `D:\代码备份\pico\pico-main\pico\cli.py`
- Read: `D:\代码备份\pico\pico-main\pico\runtime.py`
- Read: `D:\代码备份\pico\pico-main\pico\tools.py`
- Read: `D:\代码备份\pico\pico-main\pico\models.py`
- Read: `D:\代码备份\pico\pico-main\.gitignore`
- Create or update after approval: `.codecub/plan/2026-06-11-codecub-p0-1-backend-app-protocol-plan.md`
- Create or update after approval: `.codecub/plan/2026-06-11-codecub-p0-2-package-migration-plan.md`
- Create or update after approval: `.codecub/plan/2026-06-11-codecub-p0-3-desktop-shell-plan.md`
- Create or update after approval: `.codecub/plan/2026-06-11-codecub-p0-4-approval-diff-storage-safety-plan.md`
- Create or update after approval: `.codecub/plan/2026-06-11-codecub-p0-5-packaging-terminal-acceptance-plan.md`

These stage plans are mandatory gates. Each one must be written, checked for placeholders, checked against `.codecub/spec/2026-06-11-codecub-p0-requirements.md`, and approved by the user before code edits for that stage begin.

- [ ] **Step 1: Capture current status**

Run:

```powershell
git status --short
git diff --stat
```

Expected:

- Shows existing user changes.
- Shows `.codecub/` as untracked or modified.
- No files are changed by this step.

- [ ] **Step 2: Map dirty files before edits**

Run:

```powershell
git diff -- pico/cli.py pico/context_manager.py pico/memory.py pico/runtime.py tests/test_context_manager.py tests/test_memory.py tests/test_pico.py
```

Expected:

- Output identifies existing uncommitted changes.
- The engineer records which hunks must be preserved.

- [ ] **Step 3: Back up any file before modifying it**

For each existing file that will be modified, run a backup command shaped like:

```powershell
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = "E:\codex_backup\$timestamp-codecub-p0-stage-name"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$targetDir = Join-Path $backupRoot 'relative\path\parent'
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Copy-Item -LiteralPath 'relative\path\file.ext' -Destination (Join-Path $targetDir 'file.ext')
```

Expected:

- Every modified existing file has a restorable backup.
- Backup path is reported to the user before edits.

- [ ] **Step 4: Establish stage approval**

Before coding each stage, present:

- Stage name.
- Files to create.
- Files to modify.
- Files to back up.
- Tests to run.
- Expected user-visible behavior.

Expected:

- User explicitly approves before edits.

### Task 2: P0.1 Backend JSONL Protocol Subplan

**Files:**

- Create first in current package: `pico/app_protocol.py`
- Create first in current package: `pico/app_runner.py`
- Modify: `pico/cli.py`
- Modify: `pico/runtime.py`
- Test: `tests/test_app_protocol.py`
- Test: `tests/test_app_runner.py`
- Later moved by P0.2 to: `codecub/app_protocol.py`, `codecub/app_runner.py`

- [ ] **Step 1: Define event and command contracts in tests**

Create tests that assert the exact event fields:

```python
def test_event_includes_required_fields():
    event = make_event("session_started", session_id="s1", run_id="r1", payload={"cwd": "D:/repo"})
    assert event["type"] == "session_started"
    assert event["session_id"] == "s1"
    assert event["run_id"] == "r1"
    assert "timestamp" in event
    assert event["payload"] == {"cwd": "D:/repo"}
```

Create tests that assert command parsing:

```python
def test_parse_send_message_command():
    command = parse_command_line('{"type":"send_message","session_id":"s1","run_id":"r1","message":"inspect tests"}')
    assert command["type"] == "send_message"
    assert command["session_id"] == "s1"
    assert command["run_id"] == "r1"
    assert command["message"] == "inspect tests"
```

Run:

```powershell
uv run pytest tests/test_app_protocol.py -q
```

Expected:

- Fails because `pico.app_protocol` does not exist yet.

- [ ] **Step 2: Implement minimal protocol helpers**

Create `pico/app_protocol.py` with these public helpers and the exact behavior described below the signatures:

```python
def make_event(event_type, session_id="", run_id="", payload=None):
    """Return a JSON-serializable event dict with type, timestamp, session_id, run_id, and payload."""

def encode_event(event):
    """Return one compact JSON line ending with a newline."""

def parse_command_line(line):
    """Parse one JSON command line and raise ValueError for invalid commands."""
```

Required behavior:

- `make_event` returns a dict with `type`, `timestamp`, `session_id`, `run_id`, and `payload`.
- `encode_event` returns one compact JSON line ending with `\n`.
- `parse_command_line` parses one JSON command and rejects missing `type`.
- Secrets must not be introduced into event payloads by protocol helpers.

Run:

```powershell
uv run pytest tests/test_app_protocol.py -q
```

Expected:

- Protocol tests pass.

- [ ] **Step 3: Add app runner tests**

Test a fake one-shot command flow:

```python
def test_app_runner_emits_session_and_final_events(tmp_path):
    # Use FakeModelClient and a temporary workspace.
    # Send one send_message command.
    # Assert JSONL includes session_started, user_message_received, assistant_message, run_completed.
```

Test cancel command parsing:

```python
def test_app_runner_accepts_cancel_current_run_command(tmp_path):
    # Send cancel_run for the active run.
    # Assert a run_canceled event is emitted.
```

Run:

```powershell
uv run pytest tests/test_app_runner.py -q
```

Expected:

- Fails because `pico.app_runner` does not exist yet.

- [ ] **Step 4: Implement app runner loop**

Create `pico/app_runner.py` with this public entry point:

```python
def run_app_mode(args, stdin=None, stdout=None):
    """Run CodeCub app mode using JSONL commands from stdin and JSONL events to stdout."""
```

Required behavior:

- Builds the same agent as CLI mode.
- Emits `session_started`.
- Reads command JSONL from stdin.
- Handles `send_message`.
- Defines and validates `approve_operation`, `reject_operation`, `cancel_run`, and `close` command shapes.
- In P0.1, approval commands may emit a structured `run_failed` or `tool_result` response with an explicit `unsupported_until_p0_4` code if the real approval bridge is not implemented yet.
- Real blocking approval, resume-after-approval, and risky tool execution are implemented in P0.4.
- Emits `run_failed` for recoverable exceptions.
- Uses `assistant_delta` and `assistant_message` events even if the model output is internally non-streaming.

Run:

```powershell
uv run pytest tests/test_app_runner.py tests/test_app_protocol.py -q
```

Expected:

- New app protocol tests pass.

- [ ] **Step 5: Wire CLI app mode**

Modify `pico/cli.py`:

- Add `--app-mode`.
- Add `--json-events` as an alias or equivalent flag.
- When app mode is enabled, call `run_app_mode(args)` and do not print the human welcome screen.

Run:

```powershell
uv run python -m pico --help
uv run python -m pico --app-mode --cwd .
```

Expected:

- Help shows app-mode flag.
- App mode emits JSONL, not the human welcome screen.

### Task 3: P0.2 Package And Brand Migration Subplan

**Files:**

- Move directory: `pico/` to `codecub/`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `.gitignore`
- Modify tests importing `pico`
- Create compatibility shim only if approved: `pico/__init__.py`
- Test: `tests/test_pico.py`
- Test: `tests/test_safety_invariants.py`

- [ ] **Step 1: Write migration import tests**

Add tests for final package import behavior:

```python
def test_codecub_package_imports():
    import codecub
    from codecub.cli import build_arg_parser
    assert callable(build_arg_parser)
```

Add module execution test:

```python
def test_codecub_module_help_works():
    result = subprocess.run(
        [sys.executable, "-m", "codecub", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "CodeCub" in result.stdout or "codecub" in result.stdout.lower()
```

Run:

```powershell
uv run pytest tests/test_pico.py::test_codecub_package_imports tests/test_pico.py::test_codecub_module_help_works -q
```

Expected:

- Fails before migration.

- [ ] **Step 2: Rename package directory after backup**

After backing up the current `pico/` files, move package files to `codecub/`.

Expected final package:

```text
codecub/__init__.py
codecub/__main__.py
codecub/cli.py
codecub/runtime.py
codecub/tools.py
codecub/models.py
codecub/workspace.py
codecub/memory.py
codecub/context_manager.py
codecub/run_store.py
codecub/task_state.py
codecub/evaluator.py
codecub/metrics.py
codecub/app_protocol.py
codecub/app_runner.py
```

Run:

```powershell
uv run pytest tests/test_pico.py::test_codecub_package_imports -q
```

Expected:

- Import test passes.

- [ ] **Step 3: Update packaging metadata**

Modify `pyproject.toml`:

- `name = "codecub"`
- script entry `codecub = "codecub.cli:main"`
- setuptools packages include `codecub`
- old `pico` script is removed unless a compatibility alias is explicitly approved.

Run:

```powershell
uv run python -m codecub --help
uv run codecub --help
```

Expected:

- Both commands show CodeCub help.

- [ ] **Step 4: Change project data directory**

Modify runtime/session/run store defaults:

- New sessions go under `.codecub/sessions/`.
- New runs go under `.codecub/runs/`.
- Legacy `.pico/` remains readable only through the import path later implemented in P0.4.

Run:

```powershell
uv run pytest tests/test_run_store.py tests/test_pico.py -q
```

Expected:

- Tests reflect `.codecub/` for new artifacts.
- Legacy `.pico/` behavior remains covered by dedicated import tests in P0.4.

- [ ] **Step 5: Update `.gitignore` without hiding planning docs**

Modify `.gitignore` so runtime data is ignored:

```gitignore
.codecub/sessions/
.codecub/runs/
.codecub/memory/
.codecub/cache/
.codecub/logs/
```

Do not add:

```gitignore
.codecub/
```

Expected:

- `.codecub/spec/` and `.codecub/plan/` remain trackable.
- Runtime data is ignored.

### Task 4: P0.3 Desktop Shell Subplan

**Files:**

- Create: `desktop/package.json`
- Create: `desktop/tsconfig.json`
- Create: `desktop/vite.config.ts`
- Create: `desktop/electron/main.ts`
- Create: `desktop/electron/preload.ts`
- Create: `desktop/electron/backendProcess.ts`
- Create: `desktop/electron/appConfig.ts`
- Create: `desktop/electron/projectStore.ts`
- Create: `desktop/src/main.tsx`
- Create: `desktop/src/App.tsx`
- Create: `desktop/src/i18n/zh-CN.ts`
- Create: `desktop/src/i18n/en-US.ts`
- Create: `desktop/src/i18n/index.ts`
- Create: `desktop/src/state/backendEvents.ts`
- Create: `desktop/src/state/sessionIndex.ts`
- Create: `desktop/src/components/WelcomePage.tsx`
- Create: `desktop/src/components/ProjectSessionPage.tsx`
- Create: `desktop/src/components/ChatView.tsx`
- Create: `desktop/src/components/RunLogSidebar.tsx`
- Create: `desktop/src/components/SettingsPage.tsx`
- Create: `desktop/src/styles/app.css`
- Test: `desktop/tests/backendEvents.test.ts`
- Test: `desktop/tests/sessionIndex.test.ts`

- [ ] **Step 1: Ask approval before dependency setup**

Because this creates a Node/Electron project and installs dependencies, ask the user to approve:

- Target path: `D:\代码备份\pico\pico-main\desktop`
- Package manager command.
- Dependencies.
- Lockfile creation.

Expected:

- No install happens without explicit approval.

- [ ] **Step 2: Create event parser tests first**

Add test cases:

```ts
import { parseBackendEventLine } from "../src/state/backendEvents";

test("parses a run_completed event", () => {
  const event = parseBackendEventLine('{"type":"run_completed","timestamp":"2026-06-11T00:00:00Z","session_id":"s1","run_id":"r1","payload":{"final":"done"}}');
  expect(event.type).toBe("run_completed");
  expect(event.session_id).toBe("s1");
  expect(event.run_id).toBe("r1");
});
```

Run:

```powershell
cd desktop
npm test -- backendEvents.test.ts
```

Expected:

- Fails until `backendEvents.ts` exists.

- [ ] **Step 3: Implement backend event parser**

Create `desktop/src/state/backendEvents.ts` with:

```ts
export type BackendEvent = {
  type: string;
  timestamp: string;
  session_id: string;
  run_id: string;
  payload?: Record<string, unknown>;
};

export function parseBackendEventLine(line: string): BackendEvent {
  const parsed = JSON.parse(line) as Partial<BackendEvent>;
  if (!parsed.type || !parsed.timestamp || !parsed.session_id || !parsed.run_id) {
    throw new Error("backend event is missing required fields");
  }
  return parsed as BackendEvent;
}
```

Required behavior:

- Reject invalid JSON.
- Reject missing `type`, `timestamp`, `session_id`, or `run_id`.
- Preserve `payload`.

Run:

```powershell
cd desktop
npm test -- backendEvents.test.ts
```

Expected:

- Event parser tests pass.

- [ ] **Step 4: Implement minimal UI skeleton**

Create:

- `WelcomePage`
- `ProjectSessionPage`
- `ChatView`
- `RunLogSidebar`
- `SettingsPage`

Required behavior:

- Welcome page shows CodeCub brand and open project action.
- Project session page shows project path, model state, chat, log sidebar, input, Stop.
- Settings page exposes provider/model/base URL/API key source/approval/language fields.

Run:

```powershell
cd desktop
npm run build
```

Expected:

- TypeScript build passes.

- [ ] **Step 5: Implement backend subprocess manager**

Create `desktop/electron/backendProcess.ts` with:

- Dev mode command: `python -m codecub --app-mode`.
- Packaged mode command: bundled `codecub-agent.exe`.
- Event line reader.
- Stop method.
- Send command method.

Run a manual dev smoke test:

```powershell
cd desktop
npm run dev
```

Expected:

- App launches.
- Backend process starts only after project selection.
- Backend events appear in the log sidebar.

### Task 5: P0.4 Approval, Diff, Storage, And Safety Subplan

**Files:**

- Modify: `codecub/app_protocol.py`
- Modify: `codecub/app_runner.py`
- Modify: `codecub/runtime.py`
- Modify: `codecub/tools.py`
- Create: `codecub/legacy_import.py`
- Create: `tests/test_legacy_import.py`
- Modify: `desktop/electron/appConfig.ts`
- Create: `desktop/electron/secureSecrets.ts`
- Modify: `desktop/electron/projectStore.ts`
- Modify: `desktop/src/components/ApprovalDialog.tsx`
- Modify: `desktop/src/components/DiffPreviewPanel.tsx`
- Modify: `desktop/src/components/RunLogSidebar.tsx`

- [ ] **Step 1: Add approval event tests**

Backend test:

```python
def test_write_file_emits_approval_request_before_execution(tmp_path):
    # Model requests write_file.
    # App mode emits approval_requested.
    # File is not written before approval.
```

Run:

```powershell
uv run pytest tests/test_app_runner.py::test_write_file_emits_approval_request_before_execution -q
```

Expected:

- Fails before approval bridge is implemented.

- [ ] **Step 2: Implement app-mode approval bridge**

Required behavior:

- Risky tools emit `approval_requested`.
- Backend pauses until `approve_operation` or `reject_operation`.
- Reject emits `approval_resolved` and `tool_result` with rejected status.
- Approve executes the tool and emits result events.

Run:

```powershell
uv run pytest tests/test_app_runner.py tests/test_safety_invariants.py -q
```

Expected:

- Approval tests pass.
- Existing safety tests pass.

- [ ] **Step 3: Add minimal diff summary tests**

Test:

```python
def test_patch_file_emits_diff_summary(tmp_path):
    # Existing file has old text.
    # Model requests patch_file.
    # Approval request or tool result includes diff_summary for target path.
```

Run:

```powershell
uv run pytest tests/test_app_runner.py::test_patch_file_emits_diff_summary -q
```

Expected:

- Fails until diff summary is implemented.

- [ ] **Step 4: Implement diff summary**

Required behavior:

- `write_file` compares old content if file exists.
- `patch_file` produces a unified diff-like summary.
- Very large diffs are clipped with an explicit clipped marker.
- Diff event includes path, operation type, and change summary.

Run:

```powershell
uv run pytest tests/test_app_runner.py tests/test_pico.py -q
```

Expected:

- Diff tests pass.
- Existing runtime tests pass.

- [ ] **Step 5: Implement `.pico` legacy import tests**

Create `tests/test_legacy_import.py`:

```python
def test_detects_legacy_pico_directory(tmp_path):
    (tmp_path / ".pico" / "sessions").mkdir(parents=True)
    result = detect_legacy_pico(tmp_path)
    assert result.exists is True
```

```python
def test_import_does_not_delete_legacy_pico(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    import_legacy_pico(tmp_path)
    assert (tmp_path / ".pico").exists()
    assert (tmp_path / ".codecub").exists()
```

Run:

```powershell
uv run pytest tests/test_legacy_import.py -q
```

Expected:

- Fails until `codecub/legacy_import.py` exists.

- [ ] **Step 6: Implement legacy import**

Create `codecub/legacy_import.py` with:

- `detect_legacy_pico(project_root)`
- `import_legacy_pico(project_root)`

Required behavior:

- Detect `.pico/`.
- Copy importable session/run/memory artifacts into `.codecub/`.
- Do not delete or modify `.pico/`.
- Return structured import result for UI.

Run:

```powershell
uv run pytest tests/test_legacy_import.py -q
```

Expected:

- Legacy import tests pass.

- [ ] **Step 7: Implement secure secret storage strategy**

Desktop requirement:

- Do not store API keys in plain appData JSON.
- Use OS-backed secure storage if the selected Electron dependency supports it.
- If secure storage is unavailable, store only an environment variable reference and show that requirement in settings.

Tests:

```ts
test("provider config does not serialize api key into app data", () => {
  const saved = serializeProviderConfig({ provider: "openai", apiKey: "secret", apiKeySource: "secure-store" });
  expect(JSON.stringify(saved)).not.toContain("secret");
});
```

Run:

```powershell
cd desktop
npm test -- appConfig.test.ts
```

Expected:

- Secret serialization test passes.

### Task 6: P0.5 Terminal, Git, Packaging, And Acceptance Subplan

**Files:**

- Create: `desktop/electron/terminal.ts`
- Create: `desktop/electron/gitStatus.ts`
- Create: `desktop/src/components/TerminalPanel.tsx`
- Create: `desktop/src/components/GitStatusBadge.tsx`
- Modify: `desktop/package.json`
- Create packaging config after dependency approval.
- Create backend packaging script after tool approval.

- [ ] **Step 1: Ask approval for terminal dependency**

Likely dependency:

- `node-pty` or an equivalent PTY library.

Ask before installing because this downloads and builds native modules.

Expected:

- User approves dependency and install path.

- [ ] **Step 2: Implement terminal bridge tests**

Test expected bridge behavior:

```ts
test("terminal starts in selected project cwd", () => {
  const terminal = createTerminalSession({ cwd: "D:/repo" });
  expect(terminal.cwd).toBe("D:/repo");
});
```

Run:

```powershell
cd desktop
npm test -- terminal.test.ts
```

Expected:

- Fails until terminal bridge exists.

- [ ] **Step 3: Implement terminal panel**

Required behavior:

- Terminal cwd is selected project.
- User can type commands.
- Terminal output stays in terminal.
- Terminal output does not automatically enter the agent prompt.

Run:

```powershell
cd desktop
npm run build
```

Expected:

- Build passes.

- [ ] **Step 4: Implement basic Git status**

Create `desktop/electron/gitStatus.ts`:

- Read current branch.
- Detect dirty/clean.
- Count changed files.

Expected display:

- Branch name.
- Dirty badge or clean badge.
- Changed file count.

Run:

```powershell
cd desktop
npm test -- gitStatus.test.ts
```

Expected:

- Git status tests pass.

- [ ] **Step 5: Ask approval for backend executable packaging tool**

Likely tool options:

- PyInstaller.
- Nuitka.

Do not install either without explicit approval.

Expected:

- User approves tool and output path.

- [ ] **Step 6: Package backend executable**

Required packaged backend behavior:

```powershell
.\dist\codecub-agent.exe --app-mode --cwd D:\path\to\repo
```

Expected:

- Emits JSONL events.
- Does not require editable Python install.
- Does not require the source repository path.

- [ ] **Step 7: Package Windows desktop app**

Required packaged app behavior:

- Launches on Windows.
- Selects a project folder.
- Starts bundled backend.
- Shows backend events.
- Stops backend on exit.

Run:

```powershell
cd desktop
npm run package:win
```

Expected:

- Windows package is created.
- Smoke test passes.

### Task 7: Full P0 Verification Matrix

**Files:**

- Python tests under `tests/`
- Desktop tests under `desktop/tests/`
- E2E tests under `desktop/e2e/`
- Requirements: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
- Plan: `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`

- [ ] **Step 1: Python regression**

Run:

```powershell
uv run pytest -q
```

Expected:

- All backend tests pass.

- [ ] **Step 2: Desktop unit and build checks**

Run:

```powershell
cd desktop
npm test
npm run build
```

Expected:

- All desktop unit tests pass.
- TypeScript build passes.

- [ ] **Step 3: E2E acceptance**

Run the E2E suite from `desktop/e2e/` using the command defined in `desktop/package.json`:

```powershell
cd desktop
npm run e2e
```

Required covered flows:

- Open project.
- Send task.
- Streaming response display.
- Approve file write.
- Stop current task.
- Resume session.
- View logs.
- Open terminal.
- Display Git status.
- Show `.pico` import prompt.

Expected:

- All P0 E2E flows pass.

- [ ] **Step 4: Security checks**

Run targeted tests for:

- Path escape rejection.
- Secret redaction.
- Risky operation approval.
- Cancellation state recorded as canceled.
- `.pico` import failure recovery.

Expected:

- Security tests pass.
- No API key or token appears in UI logs, debug logs, trace, or reports.

- [ ] **Step 5: Windows package smoke test**

Run packaged app manually or by automation:

- Install or launch package.
- Open a test project.
- Send one read-only task.
- Send one risky write task and reject it.
- Send one risky write task and approve it.
- Stop one running task.
- Exit app.

Expected:

- Bundled backend starts.
- Rejected write does not modify file.
- Approved write modifies only selected project.
- Stop records canceled state.
- App exits and cleans child processes.

## 3. Plan Review

### Spec Coverage

Covered requirements:

- Product positioning and pet theme: Tasks 3 and 4 cover branding and UI placement.
- P0/P1 boundary: Section 0 and staged execution rules cover scope.
- JSONL backend protocol: Task 2.
- Package migration: Task 3.
- Electron shell: Task 4.
- Approval, logs, diff, safety: Task 5.
- Storage, secure secrets, session index, `.pico` import: Task 5.
- Streaming: Task 2 and Task 7.
- Terminal, Git, packaging: Task 6.
- Acceptance matrix: Task 7.
- Dirty worktree and backups: Task 1.

Known gaps intentionally left for stage subplans:

- Exact Electron dependency versions will be chosen only after user approval for dependency installation.
- Exact Windows packaging tool will be chosen only after user approval for PyInstaller, Nuitka, or another backend packaging tool.
- Exact E2E framework will be chosen in the desktop subplan after dependency approval, and its tests will live under `desktop/e2e/`.
- Comment-only test sketches in this master plan are not sufficient for implementation. The relevant stage plan must replace them with complete test code before that stage starts.
- P0.1 defines approval command shapes, while P0.4 implements real approval blocking and continuation.

These are not implementation gaps because the P0 requirement explicitly requires approval before installing tools or dependencies.

### Placeholder Scan

This plan avoids unresolved placeholder markers and unspecified "handle later" instructions. Where a choice requires user approval, the plan states the approval gate and the allowed decision boundary.

### Type And Path Consistency

Backend paths use current `pico/` before P0.2 and final `codecub/` after P0.2. Desktop paths consistently live under `desktop/`. Project planning artifacts remain under `.codecub/spec/` and `.codecub/plan/`; runtime state is planned under specific ignored `.codecub/*` subdirectories rather than ignoring the entire `.codecub/` tree.
