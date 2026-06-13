# CodeCub P0.4 Approval, Diff, Storage, and Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable desktop safety loop: app-mode approval requests, approve/reject commands, diff summaries, `.pico` import detection, desktop approval UI, and safer settings handling.

**Architecture:** Keep the Python runtime as the source of truth for safety decisions. `codecub.app_runner` becomes a small command/event coordinator that runs `agent.ask()` in a worker thread while the main thread keeps reading stdin commands from Electron. `codecub.runtime.Pico` receives an optional approval callback; CLI behavior stays unchanged when no callback is provided.

**Tech Stack:** Python standard library threads/queues, existing JSONL app protocol, existing `Pico` runtime, Electron IPC, Vite React TypeScript, Vitest, pytest.

---

## Understood Requirement

P0.4 must close the gap left by P0.3:

- Default approval policy remains `ask`.
- Read-only tools continue without approval.
- Risky tools (`run_shell`, `write_file`, `patch_file`) produce an approval request in app mode.
- Desktop can approve or reject that request while the backend run is still blocked.
- Rejecting an approval returns a normal tool denial to the agent and does not mutate the workspace.
- Approval result and tool result are emitted as JSONL events suitable for the desktop run log.
- Risky tool results include affected paths and diff summary data already produced by runtime snapshots, and app-mode emits a dedicated `diff_summary` event for UI consumers that do not want to parse tool payloads.
- `.pico` sessions are detected and importable into `.codecub` only after the desktop prompts the user and receives confirmation. Import must not modify or delete `.pico`.
- Run log debug mode exposes structured payloads, stdout/stderr snippets, trace/report paths, error codes, and approval state without exposing secrets.
- Desktop settings do not persist API keys in project files or app config. P0.4 uses environment-only secret handling to avoid adding a new native secure-storage dependency.
- Existing P0.1-P0.3 behavior must remain runnable.

## Confirmed Scope

In scope:

- Backend protocol additions for `approval_requested`, approval completion, and import status.
- Backend protocol additions for `import_legacy_pico` command and `diff_summary` event.
- Runtime approval callback injection.
- App-mode command loop concurrency sufficient for one active run.
- Desktop approval dialog, diff preview panel, and richer run log rendering.
- Desktop `.pico` import prompt and import result display.
- `.pico` import detection/copy tests.
- Unit tests and smoke tests.

Out of scope for P0.4:

- Guaranteed termination of arbitrary child processes already inside blocking `subprocess.run()`. P0.4 still attempts cancellation by rejecting pending approvals, marking the run canceled, and preventing additional approved work.
- Multi-project concurrent runs.
- Native keychain integration.
- Full release packaging.
- Real model streaming token-by-token. P0.4 can keep whole-answer `assistant_delta` as P0.3 did.

## Files and Responsibilities

- Modify `codecub/app_protocol.py`: extend event type validation to include approval and legacy import events.
- Modify `codecub/app_protocol.py`: extend command parsing to include `import_legacy_pico`.
- Modify `codecub/app_runner.py`: coordinate one active worker run, pending approval decisions, cancel flags, legacy import command handling, and event emission.
- Modify `codecub/runtime.py`: accept `approval_handler`; delegate risky-tool approval to it under `approval_policy == "ask"`.
- Create `codecub/legacy_import.py`: detect `.pico/sessions`, copy/import JSON sessions into `.codecub/sessions`, and report summary.
- Modify `desktop/electron/ipcTypes.ts`: add approval command/event-friendly types.
- Modify `desktop/electron/appConfig.ts`: store only language, approval policy, and recent projects; explicitly exclude API key values.
- Modify `desktop/electron/backendProcess.ts`: pass approval policy to backend startup and expose command sending unchanged.
- Modify `desktop/src/state/backendEvents.ts`: parse new backend event types.
- Modify `desktop/src/state/chatState.ts`: preserve running state while approval is pending.
- Create `desktop/src/state/approvalState.ts`: track pending approval cards by `approval_id`.
- Modify `desktop/src/App.tsx`: wire approval state and command handlers.
- Modify `desktop/src/components/ProjectSessionPage.tsx`: place approval/diff panels beside chat/log.
- Create `desktop/src/components/ApprovalDialog.tsx`: render risky operation details and approve/reject buttons.
- Create `desktop/src/components/DiffPreviewPanel.tsx`: render affected paths and summary counts.
- Create `desktop/src/components/LegacyImportPrompt.tsx`: prompt before importing `.pico` data and render import result/failure.
- Modify `desktop/src/components/RunLogSidebar.tsx`: render approval, rejection, cancellation, and tool result statuses.
- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`: add bilingual strings; default UI remains Chinese.
- Modify `desktop/src/styles/app.css`: add compact, non-card-nested approval/diff layouts.
- Modify `tests/test_app_protocol.py`: protocol tests for new event types.
- Modify `tests/test_app_runner.py`: app-mode approve/reject/cancel tests.
- Modify `tests/test_safety_invariants.py`: runtime approval callback tests.
- Create `tests/test_legacy_import.py`: `.pico` import tests.
- Modify `desktop/src/state/*.test.ts`: frontend state tests for approval/diff events.

## Backup Requirement Before Execution

Before modifying each existing file, create a timestamped backup under `E:\codex_backup`, preserving enough path information to restore the original file. Example folder name:

```text
E:\codex_backup\20260612-<HHMMSS>-codecub-p0-4-approval-safety
```

New files do not need existing-file backups.

## Stop Conditions

Stop, report, and write a repair plan before continuing if any of these occur:

- Worker-thread app-mode cannot receive `approve_operation` while `agent.ask()` is blocked.
- Approval rejection still mutates workspace in a test.
- `.pico` import writes to or deletes anything under `.pico`.
- Desktop needs a native secure-storage dependency to meet the P0 requirement; ask before installing.
- Existing Python regression count drops for unrelated safety tests.
- Existing desktop typecheck/build fails because the P0.3 shell structure is incompatible with approval UI state.

---

### Task 1: Protocol Events for Approval and Legacy Import

**Files:**
- Modify: `codecub/app_protocol.py`
- Modify: `tests/test_app_protocol.py`

- [ ] **Step 1: Back up existing files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-protocol"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "codecub\app_protocol.py") -Destination "$backup\codecub_app_protocol.py"
Copy-Item -LiteralPath (Join-Path $repo "tests\test_app_protocol.py") -Destination "$backup\tests_test_app_protocol.py"
```

Expected: backup directory exists with two files.

- [ ] **Step 2: Write failing protocol tests**

Add tests equivalent to:

```python
def test_make_event_accepts_approval_and_import_event_types():
    approval = make_event(
        "approval_requested",
        session_id="session-1",
        run_id="run-1",
        payload={"approval_id": "approval-1", "tool_name": "write_file"},
    )
    imported = make_event(
        "legacy_import_completed",
        session_id="session-1",
        payload={"imported_count": 2, "skipped_count": 1},
    )

    assert approval["type"] == "approval_requested"
    assert imported["type"] == "legacy_import_completed"


def test_parse_import_legacy_pico_command():
    command = parse_command_line('{"type":"import_legacy_pico","session_id":"session-1"}')

    assert command["type"] == "import_legacy_pico"
    assert command["session_id"] == "session-1"
```

Run:

```powershell
uv run pytest tests/test_app_protocol.py::test_make_event_accepts_approval_and_import_event_types -q
```

Expected before implementation: fails with unknown event type.

- [ ] **Step 3: Extend valid event types**

Add these event names to the protocol allowlist:

```python
"approval_requested",
"approval_resolved",
"diff_summary",
"legacy_import_detected",
"legacy_import_completed",
"legacy_import_failed",
```

Expected behavior: `make_event()` accepts the new event names, and `parse_command_line()` accepts `import_legacy_pico`.

- [ ] **Step 4: Verify protocol tests**

Run:

```powershell
uv run pytest tests/test_app_protocol.py -q
```

Expected: all protocol tests pass.

---

### Task 2: Runtime Approval Callback

**Files:**
- Modify: `codecub/runtime.py`
- Modify: `tests/test_safety_invariants.py`

- [ ] **Step 1: Back up existing files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-runtime-approval"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "codecub\runtime.py") -Destination "$backup\codecub_runtime.py"
Copy-Item -LiteralPath (Join-Path $repo "tests\test_safety_invariants.py") -Destination "$backup\tests_test_safety_invariants.py"
```

- [ ] **Step 2: Write failing callback tests**

Add tests equivalent to:

```python
def test_ask_approval_policy_delegates_risky_tool_to_callback(tmp_path):
    decisions = []
    agent = build_agent(tmp_path, [], approval_policy="ask")
    agent.approval_handler = lambda name, args, runtime: decisions.append((name, args["path"])) or True

    result = agent.run_tool("write_file", {"path": "approved.txt", "content": "ok\n"})

    assert decisions == [("write_file", "approved.txt")]
    assert "wrote approved.txt" in result
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok\n"


def test_rejected_approval_callback_blocks_risky_tool_without_mutation(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="ask")
    agent.approval_handler = lambda name, args, runtime: False

    result = agent.run_tool("write_file", {"path": "blocked.txt", "content": "no\n"})

    assert result == "error: approval denied for write_file"
    assert not (tmp_path / "blocked.txt").exists()
    assert agent._last_tool_result_metadata["tool_error_code"] == "approval_denied"
```

Run:

```powershell
uv run pytest tests/test_safety_invariants.py::test_ask_approval_policy_delegates_risky_tool_to_callback tests/test_safety_invariants.py::test_rejected_approval_callback_blocks_risky_tool_without_mutation -q
```

Expected before implementation: fails because `approval_handler` is not used.

- [ ] **Step 3: Add constructor argument and approve branch**

Implement in `Pico.__init__`:

```python
approval_handler=None,
```

and:

```python
self.approval_handler = approval_handler
```

Update `approve()` so the `ask` path is:

```python
if self.approval_policy == "ask" and self.approval_handler is not None:
    return bool(self.approval_handler(name, args, self))
```

Keep existing `input()` fallback for normal CLI.

- [ ] **Step 4: Verify runtime approval tests**

Run:

```powershell
uv run pytest tests/test_safety_invariants.py::test_ask_approval_policy_delegates_risky_tool_to_callback tests/test_safety_invariants.py::test_rejected_approval_callback_blocks_risky_tool_without_mutation -q
```

Expected: both tests pass.

---

### Task 3: App-Mode Worker and Approval Coordination

**Files:**
- Modify: `codecub/app_runner.py`
- Modify: `tests/test_app_runner.py`

- [ ] **Step 1: Back up existing files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-app-runner"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "codecub\app_runner.py") -Destination "$backup\codecub_app_runner.py"
Copy-Item -LiteralPath (Join-Path $repo "tests\test_app_runner.py") -Destination "$backup\tests_test_app_runner.py"
```

- [ ] **Step 2: Replace unsupported approval test with approve/reject behavior tests**

Add a test using a fake model output that requests a risky tool:

```python
def test_app_runner_approves_pending_risky_tool(tmp_path):
    stdin = io.StringIO(
        '{"type":"send_message","run_id":"run-1","message":"write"}\n'
        '{"type":"approve_operation","run_id":"run-1","approval_id":"approval-1"}\n'
        '{"type":"close"}\n'
    )
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([
            '<tool name="write_file" path="approved.txt"><content>ok\\n</content></tool>',
            "<final>done</final>",
        ]),
    )

    events = parse_jsonl(stdout.getvalue())
    assert exit_code == 0
    assert any(event["type"] == "approval_requested" for event in events)
    assert any(event["type"] == "approval_resolved" and event["payload"]["decision"] == "approved" for event in events)
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok\n"
```

Add a reject test:

```python
def test_app_runner_rejects_pending_risky_tool_without_mutation(tmp_path):
    stdin = io.StringIO(
        '{"type":"send_message","run_id":"run-1","message":"write"}\n'
        '{"type":"reject_operation","run_id":"run-1","approval_id":"approval-1","reason":"too risky"}\n'
        '{"type":"close"}\n'
    )
    stdout = io.StringIO()

    run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory([
            '<tool name="write_file" path="rejected.txt"><content>no\\n</content></tool>',
            "<final>handled rejection</final>",
        ]),
    )

    events = parse_jsonl(stdout.getvalue())
    assert any(event["type"] == "approval_requested" for event in events)
    assert any(event["type"] == "approval_resolved" and event["payload"]["decision"] == "rejected" for event in events)
    assert not (tmp_path / "rejected.txt").exists()
```

Run both tests. Expected before implementation: fail because approval commands are still unsupported.

- [ ] **Step 3: Implement coordinator state**

Introduce small internal classes in `app_runner.py`:

```python
class ApprovalRequest:
    def __init__(self, approval_id, run_id, tool_name, args):
        self.approval_id = approval_id
        self.run_id = run_id
        self.tool_name = tool_name
        self.args = args
        self.event = threading.Event()
        self.approved = False
        self.reason = ""
```

Use:

```python
pending_approvals = {}
pending_lock = threading.Lock()
output_lock = threading.Lock()
active_run = {"run_id": "", "thread": None, "cancel_requested": False}
```

All writes to stdout must go through `_write_event()` while holding `output_lock`.

- [ ] **Step 4: Implement approval handler**

Approval handler behavior:

1. Generate approval id using deterministic counter: `approval-1`, `approval-2`, etc.
2. Store `ApprovalRequest` in `pending_approvals`.
3. Emit `approval_requested` with payload:

```python
{
    "approval_id": approval_id,
    "tool_name": name,
    "args": args,
    "cwd": str(agent.root),
    "timeout": args.get("timeout", None),
    "diff_summary": [],
    "risk_level": "high",
}
```

4. Block on `request.event.wait()`.
5. Emit `approval_resolved` with `decision` set to `approved` or `rejected`.
6. Remove request from `pending_approvals`.
7. Return `request.approved`.

- [ ] **Step 5: Run `agent.ask()` in a worker thread**

For `send_message`, set `active_run["run_id"]`, emit `user_message_received`, and start a `threading.Thread` target that:

```python
try:
    answer = agent.ask(message)
except Exception as exc:
    emit run_failed
else:
    emit assistant_delta
    emit assistant_message
    emit run_completed
finally:
    clear active_run if run id matches
```

The main stdin loop continues reading `approve_operation`, `reject_operation`, `cancel_run`, and `close`.

- [ ] **Step 6: Resolve approval commands**

For `approve_operation` and `reject_operation`:

- Look up `approval_id`.
- If missing, emit `tool_result` with status `error`, code `unknown_approval`.
- If found and run id mismatches, emit `tool_result` with status `error`, code `run_mismatch`.
- Otherwise set `approved` and `reason`, then call `event.set()`.

- [ ] **Step 7: Handle close and cancellation**

On `cancel_run`, set `active_run["cancel_requested"] = True`, emit `run_canceled`, and reject all pending approvals for that run with reason `user_requested`.

Cancellation attempt semantics for P0.4:

- If the run is waiting on approval, rejecting the approval prevents the risky tool from executing.
- If the run has not entered a tool call yet, the worker must observe `cancel_requested` before emitting success and instead stop as canceled.
- If the run is already inside blocking `subprocess.run()`, P0.4 records the cancellation request and does not claim the subprocess was terminated.
- In all cases, the UI-visible final state for that run must not be `run_completed` after a `run_canceled` event for the same run.

On `close`, reject all pending approvals with reason `session_closed`, wait up to two seconds for the active worker to finish, emit `session_closed`, and return.

- [ ] **Step 8: Verify app runner tests**

Run:

```powershell
uv run pytest tests/test_app_runner.py -q
```

Expected: all app runner tests pass.

---

### Task 4: Tool Result Diff Event Exposure

**Files:**
- Modify: `codecub/app_runner.py`
- Modify: `tests/test_app_runner.py`

- [ ] **Step 1: Add app runner diff assertion**

Extend the approved write test to assert a completion or tool-result-like event includes runtime metadata:

```python
tool_events = [event for event in events if event["type"] == "tool_result"]
assert any("approved.txt" in path for event in tool_events for path in event["payload"].get("affected_paths", []))
assert any(event["payload"].get("workspace_changed") is True for event in tool_events)
diff_events = [event for event in events if event["type"] == "diff_summary"]
assert any("approved.txt" in path for event in diff_events for path in event["payload"].get("affected_paths", []))
```

Expected before implementation: fails if no `tool_result` or dedicated `diff_summary` event is emitted from app mode.

- [ ] **Step 2: Emit tool result after each tool execution**

Use `Pico.emit_trace()` as the least invasive hook only if already called after `run_tool()`. If that hook is too broad, add an optional `event_handler` callback to `Pico.__init__` and call it immediately after the existing `tool_executed` trace payload is built in `ask()`.

`tool_result` payload shape:

```python
{
    "tool_name": name,
    "args": args,
    "result": result,
    "status": metadata.get("tool_status", ""),
    "code": metadata.get("tool_error_code", ""),
    "affected_paths": metadata.get("affected_paths", []),
    "workspace_changed": metadata.get("workspace_changed", False),
    "diff_summary": metadata.get("diff_summary", []),
}
```

When `workspace_changed` is true or `diff_summary` is non-empty, also emit a dedicated `diff_summary` event:

```python
{
    "tool_name": name,
    "affected_paths": metadata.get("affected_paths", []),
    "workspace_changed": metadata.get("workspace_changed", False),
    "diff_summary": metadata.get("diff_summary", []),
}
```

Sensitive values must be redacted by existing runtime redaction helpers where available; if no helper is directly reusable, do not include environment values or shell env in this event.

- [ ] **Step 3: Expose run artifact paths for debug mode**

When emitting `run_completed`, `run_failed`, or `run_canceled`, include artifact paths if the runtime has produced them:

```python
{
    "run_dir": str(agent.current_run_dir) if agent.current_run_dir else "",
    "trace_path": str(agent.current_run_dir / "trace.jsonl") if agent.current_run_dir else "",
    "report_path": str(agent.current_run_dir / "report.json") if agent.current_run_dir else "",
}
```

If a path does not exist, include an empty string rather than raising.

- [ ] **Step 4: Verify diff metadata tests**

Run:

```powershell
uv run pytest tests/test_app_runner.py::test_app_runner_approves_pending_risky_tool -q
```

Expected: approved write produces both `tool_result` and `diff_summary` with changed path and diff summary.

---

### Task 5: `.pico` Legacy Import

**Files:**
- Create: `codecub/legacy_import.py`
- Create: `tests/test_legacy_import.py`
- Modify: `codecub/app_protocol.py`
- Modify: `codecub/app_runner.py`

- [ ] **Step 1: Write legacy import tests**

Create tests:

```python
from codecub.legacy_import import detect_legacy_pico, import_legacy_pico_sessions


def test_detect_legacy_pico_sessions(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text('{"id":"old","history":[]}', encoding="utf-8")

    result = detect_legacy_pico(tmp_path)

    assert result["exists"] is True
    assert result["session_count"] == 1


def test_import_legacy_pico_sessions_copies_without_modifying_source(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    source = legacy / "old.json"
    source.write_text('{"id":"old","history":[]}', encoding="utf-8")

    summary = import_legacy_pico_sessions(tmp_path)

    assert summary["imported_count"] == 1
    assert summary["skipped_count"] == 0
    assert source.read_text(encoding="utf-8") == '{"id":"old","history":[]}'
    assert (tmp_path / ".codecub" / "sessions" / "old.json").exists()
```

Run:

```powershell
uv run pytest tests/test_legacy_import.py -q
```

Expected before implementation: import error.

- [ ] **Step 2: Add app protocol command test**

In `tests/test_app_protocol.py`, ensure this behavior exists:

```python
def test_parse_import_legacy_pico_command():
    command = parse_command_line('{"type":"import_legacy_pico","session_id":"session-1"}')

    assert command["type"] == "import_legacy_pico"
    assert command["session_id"] == "session-1"
```

Run:

```powershell
uv run pytest tests/test_app_protocol.py::test_parse_import_legacy_pico_command -q
```

Expected before implementation: fails until command allowlist includes `import_legacy_pico`.

- [ ] **Step 3: Implement legacy import module**

Implement:

```python
def detect_legacy_pico(project_root):
    root = Path(project_root)
    session_dir = root / ".pico" / "sessions"
    files = sorted(session_dir.glob("*.json")) if session_dir.is_dir() else []
    return {"exists": session_dir.is_dir(), "session_count": len(files), "session_paths": [str(path) for path in files]}
```

Implement copy import:

```python
def import_legacy_pico_sessions(project_root):
    root = Path(project_root)
    source_dir = root / ".pico" / "sessions"
    target_dir = root / ".codecub" / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    imported = 0
    skipped = 0
    errors = []
    for source in sorted(source_dir.glob("*.json")) if source_dir.is_dir() else []:
        target = target_dir / source.name
        if target.exists():
            skipped += 1
            continue
        try:
            json.loads(source.read_text(encoding="utf-8"))
            shutil.copy2(source, target)
            imported += 1
        except Exception as exc:
            errors.append({"path": str(source), "message": str(exc)})
    return {"imported_count": imported, "skipped_count": skipped, "errors": errors}
```

- [ ] **Step 4: Expose detection through app-mode startup**

After `session_started`, emit `legacy_import_detected` when `.pico/sessions/*.json` exists:

```python
{
    "exists": True,
    "session_count": 3,
}
```

- [ ] **Step 5: Expose confirmed import through app-mode command**

When app-mode receives:

```json
{"type":"import_legacy_pico","session_id":"session-1"}
```

call `import_legacy_pico_sessions(agent.root)` and emit exactly one completion event:

```python
_write_event(
    stdout,
    "legacy_import_completed",
    session_id=session_id,
    payload={
        "imported_count": summary["imported_count"],
        "skipped_count": summary["skipped_count"],
        "errors": summary["errors"],
    },
)
```

If import raises unexpectedly, emit:

```python
_write_event(
    stdout,
    "legacy_import_failed",
    session_id=session_id,
    payload={"error_type": exc.__class__.__name__, "message": str(exc)},
)
```

Do not import automatically on startup. Startup only detects and lets the desktop ask the user.

- [ ] **Step 6: Verify legacy tests**

Run:

```powershell
uv run pytest tests/test_legacy_import.py tests/test_app_runner.py -q
```

Expected: tests pass and `.pico` source files remain unchanged.

---

### Task 6: Desktop Approval and Diff State

**Files:**
- Modify: `desktop/electron/ipcTypes.ts`
- Modify: `desktop/src/state/backendEvents.ts`
- Create: `desktop/src/state/approvalState.ts`
- Create: `desktop/src/state/approvalState.test.ts`
- Modify: `desktop/src/state/chatState.ts`
- Modify: `desktop/src/state/chatState.test.ts`

- [ ] **Step 1: Back up existing desktop state files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-desktop-state"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\state\backendEvents.ts") -Destination "$backup\desktop_src_state_backendEvents.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\state\chatState.ts") -Destination "$backup\desktop_src_state_chatState.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\state\chatState.test.ts") -Destination "$backup\desktop_src_state_chatState.test.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\ipcTypes.ts") -Destination "$backup\desktop_electron_ipcTypes.ts"
```

- [ ] **Step 2: Add approval state reducer tests**

Create tests asserting:

```ts
const requested = applyApprovalEvent(createInitialApprovalState(), {
  type: "approval_requested",
  session_id: "s",
  run_id: "run-1",
  timestamp: "2026-06-12T00:00:00Z",
  payload: { approval_id: "approval-1", tool_name: "write_file", args: { path: "README.md" } },
});
expect(requested.pending).toHaveLength(1);

const resolved = applyApprovalEvent(requested, {
  type: "approval_resolved",
  session_id: "s",
  run_id: "run-1",
  timestamp: "2026-06-12T00:00:01Z",
  payload: { approval_id: "approval-1", decision: "approved" },
});
expect(resolved.pending).toHaveLength(0);
expect(resolved.history[0].decision).toBe("approved");
```

Run:

```powershell
cd desktop
npm test -- approvalState
```

Expected before implementation: file missing.

- [ ] **Step 3: Implement backend event type additions**

Add discriminated event support for:

```ts
"approval_requested" | "approval_resolved" | "tool_result" | "diff_summary" | "legacy_import_detected" | "legacy_import_completed" | "legacy_import_failed"
```

Use `unknown`-safe payload access in reducers; do not assume payload fields are present.

Update `BackendCommand` in `desktop/electron/ipcTypes.ts` to include:

```ts
| { type: "import_legacy_pico"; session_id?: string }
```

- [ ] **Step 4: Implement approval state reducer**

State shape:

```ts
export type ApprovalState = {
  pending: PendingApproval[];
  history: ResolvedApproval[];
};
```

Reducer behavior:

- `approval_requested`: add or replace by `approvalId`.
- `approval_resolved`: remove from pending; append to history.
- `run_canceled` and `run_failed`: remove approvals for that run.

- [ ] **Step 5: Verify desktop state tests**

Run:

```powershell
cd desktop
npm test
```

Expected: all Vitest tests pass.

---

### Task 7: Desktop Approval Dialog, Diff Preview, and Run Log

**Files:**
- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/components/ProjectSessionPage.tsx`
- Create: `desktop/src/components/ApprovalDialog.tsx`
- Create: `desktop/src/components/DiffPreviewPanel.tsx`
- Create: `desktop/src/components/LegacyImportPrompt.tsx`
- Modify: `desktop/src/components/RunLogSidebar.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Modify: `desktop/src/styles/app.css`

- [ ] **Step 1: Back up existing desktop UI files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-desktop-ui"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\App.tsx") -Destination "$backup\desktop_src_App.tsx"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\components\ProjectSessionPage.tsx") -Destination "$backup\desktop_src_components_ProjectSessionPage.tsx"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\components\RunLogSidebar.tsx") -Destination "$backup\desktop_src_components_RunLogSidebar.tsx"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\i18n\zh-CN.ts") -Destination "$backup\desktop_src_i18n_zh-CN.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\i18n\en-US.ts") -Destination "$backup\desktop_src_i18n_en-US.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\styles\app.css") -Destination "$backup\desktop_src_styles_app.css"
```

- [ ] **Step 2: Implement approval command handlers**

In `App.tsx`, add:

```ts
const approveOperation = async (approvalId: string, runId: string) => {
  await window.codecub.sendBackendCommand({ type: "approve_operation", run_id: runId, approval_id: approvalId });
};

const rejectOperation = async (approvalId: string, runId: string, reason: string) => {
  await window.codecub.sendBackendCommand({
    type: "reject_operation",
    run_id: runId,
    approval_id: approvalId,
    reason,
  });
};
```

- [ ] **Step 3: Implement `ApprovalDialog`**

Required UI:

- Show tool name.
- Show target path if `args.path` exists.
- Show shell command if `args.command` exists.
- Show cwd if present.
- Show timeout if present.
- Show diff summary if present.
- Show risk summary from `risk_level`.
- Provide Approve and Reject buttons.
- Disable buttons after click until backend emits `approval_resolved`.

Use Chinese labels from i18n by default.

- [ ] **Step 4: Implement `DiffPreviewPanel`**

Input events: recent `tool_result` events.

Render:

- affected paths
- workspace changed yes/no
- diff summary counts when available
- empty state text when no risky tool has changed files

- [ ] **Step 5: Implement `LegacyImportPrompt`**

Required UI behavior:

- Show only after `legacy_import_detected` reports `exists: true` and `session_count > 0`.
- Explain in Chinese that legacy `.pico` data can be copied into `.codecub` and the original `.pico` folder will not be modified.
- Provide Import and Dismiss buttons.
- Import button sends:

```ts
await window.codecub.sendBackendCommand({ type: "import_legacy_pico" });
```

- On `legacy_import_completed`, show imported/skipped counts and hide the prompt.
- On `legacy_import_failed`, show the error message and keep the original `.pico` data untouched.

- [ ] **Step 6: Update run log sidebar**

Run log should render distinct rows for:

- `approval_requested`
- `approval_resolved`
- `tool_result`
- `diff_summary`
- `legacy_import_detected`
- `legacy_import_completed`
- `legacy_import_failed`
- `run_canceled`
- `run_failed`

Do not render full API keys, tokens, environment maps, or long command output by default.

- [ ] **Step 7: Add debug log mode**

Add a compact debug toggle near the run log. When enabled, each selected backend event row can expand to show:

- sanitized payload JSON
- stdout/stderr snippets if present in payload
- trace/report paths if present in payload
- error code and error type
- approval id, approval state, and decision

Debug mode must still redact likely secrets. At minimum, hide values whose key contains `api_key`, `token`, `secret`, or `password`, case-insensitive.

- [ ] **Step 8: Verify desktop build**

Run:

```powershell
cd desktop
npm run typecheck
npm run build
```

Expected: both commands succeed.

---

### Task 8: Settings and Secret Storage Safety

**Files:**
- Modify: `desktop/electron/appConfig.ts`
- Modify: `desktop/electron/backendProcess.ts`
- Modify: `desktop/electron/ipcTypes.ts`
- Modify: `desktop/src/components/SettingsPage.tsx`

- [ ] **Step 1: Back up settings files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-4-settings-safety"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\appConfig.ts") -Destination "$backup\desktop_electron_appConfig.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\backendProcess.ts") -Destination "$backup\desktop_electron_backendProcess.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\ipcTypes.ts") -Destination "$backup\desktop_electron_ipcTypes.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\components\SettingsPage.tsx") -Destination "$backup\desktop_src_components_SettingsPage.tsx"
```

- [ ] **Step 2: Enforce non-secret settings**

Ensure `AppSettings` only includes:

```ts
{
  language: "zh-CN" | "en-US";
  approvalPolicy: "ask" | "auto" | "never";
  recentProjects: string[];
}
```

Add an inline comment in `appConfig.ts`:

```ts
// P0 keeps provider secrets in the parent process environment only; do not persist API keys here.
```

- [ ] **Step 3: Pass approval policy into backend command**

Update backend spawn arguments to include:

```ts
"--approval", settings.approvalPolicy
```

If `BackendProcess` does not have settings access, pass approval policy through `start(projectPath, approvalPolicy)`.

- [ ] **Step 4: Verify no secret keys are persisted**

Run:

```powershell
rg -n "apiKey|api_key|OPENAI_API_KEY|DASHSCOPE_API_KEY|token|secret" desktop/electron desktop/src
```

Expected: only explanatory text/comments or environment references; no setting field storing a secret value.

---

### Task 9: Full Verification

**Files:**
- No new file edits unless a verification failure identifies a scoped fix.

- [ ] **Step 1: Python focused tests**

Run:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_safety_invariants.py tests/test_legacy_import.py tests/test_workspace.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Python broader regression**

Run:

```powershell
uv run pytest -q
```

Expected: same or better than current baseline `113 passed, 2 skipped`. If failures are unrelated environment/model failures, record exact failure names and stop for review.

- [ ] **Step 3: Desktop tests and build**

Run:

```powershell
cd desktop
npm test
npm run typecheck
npm run build
```

Expected: all commands pass.

- [ ] **Step 4: Backend app-mode smoke with Qwen-compatible OpenAI provider settings**

Run only if the required API environment is already present:

```powershell
$env:OPENAI_MODEL="qwen-flash"
$repo = (Resolve-Path ".").Path
uv run python -m codecub --app-mode --cwd $repo --approval ask
```

Manual JSONL interaction:

```json
{"type":"send_message","run_id":"run-smoke","message":"只读取 README.md 前 5 行，不要修改文件"}
{"type":"close"}
```

Expected: session starts, user message is accepted, run completes or fails with a model/provider error that does not crash app-mode.

- [ ] **Step 5: Electron smoke**

Run:

```powershell
cd desktop
npm run build
node_modules\electron\dist\electron.exe dist-electron\main.js --disable-gpu
```

Expected: app launches without immediate main-process crash. If Browser/Chrome control tools become available, perform screenshot and click-through verification of approval dialog.

---

## Plan Review

- Requirement match: The plan covers approval ask flow, reject safety, diff metadata exposure, `.pico` import detection/copy, bilingual desktop UI additions, and settings secret safety.
- Remaining uncertainty: Full subprocess termination cannot be guaranteed in P0.4 because existing `tool_run_shell()` uses blocking `subprocess.run()` without a process handle exposed to app-mode. The plan still requires a cancellation attempt: reject pending approvals, set a run cancellation flag, prevent a canceled run from later emitting success, and record the limitation when a command is already inside blocking shell execution.
- Structural risk: The app runner threading change is the highest-risk part. It is constrained to `codecub/app_runner.py` plus a callback seam in `codecub/runtime.py`, preserving CLI fallback behavior.
- Maintenance check: The plan avoids adding native dependencies and keeps `.pico` import as a small isolated module. Desktop approval state is split into a reducer so UI components do not parse raw backend events directly.
- Placeholder scan: No unresolved implementation placeholders are intentionally left in the plan.

## Execution Choice

Recommended execution for this repo: inline execution with `superpowers:executing-plans`, one task at a time, stopping after Task 3 if approval coordination cannot be proven by tests.

---

## Execution Status

Status: completed on 2026-06-12.

Implemented:

- Backend protocol now validates known event types and accepts `import_legacy_pico`.
- Runtime supports `approval_handler` and `event_handler` injection while preserving CLI stdin approval fallback.
- App-mode now runs `agent.ask()` in a worker thread so stdin can continue receiving approve/reject/cancel/close commands.
- Risky app-mode tools emit `approval_requested`, block until approve/reject, then emit `approval_resolved`.
- Tool execution emits `tool_result`; file-changing tools also emit dedicated `diff_summary`.
- Legacy `.pico/sessions/*.json` data is detected on startup and copied only after `import_legacy_pico`.
- Desktop has approval state, approval dialog, diff preview, legacy import prompt, debug run log, and approval-policy startup wiring.
- Desktop settings persist only language and approval policy; provider secrets remain environment-only.

Verification:

- `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_safety_invariants.py tests/test_legacy_import.py tests/test_workspace.py -q`: 32 passed, 1 skipped.
- `cd desktop && npm test`: 4 files, 8 tests passed.
- `cd desktop && npm run typecheck`: passed.
- `cd desktop && npm run build`: passed.
- Electron smoke with built `dist-electron/main.js`: process stayed alive after 6 seconds and was then stopped.
- Secret persistence scan: no API key/token/secret setting fields found; only explanatory text and debug redaction logic matched.

Known verification limitations:

- Qwen app-mode smoke started without immediate crash, but the live model subprocess did not finish before the 184 second outer timeout. The two leftover app-mode Python processes were identified and stopped.

---

## Acceptance Fix Status

Status: completed on 2026-06-12 after P0.4 acceptance review.

Fixed after review:

- `codecub/evaluator.py` no longer depends on installed `tzdata` for `Asia/Shanghai`; it falls back to fixed UTC+8.
- Benchmark verifier commands that start with `python` or `python3 -c` now run through the current Python interpreter, avoiding Windows `python3` alias failures.
- Benchmark verifier code now maps legacy `.pico/` paths to `.codecub/`, matching the package migration completed in P0.2.
- Benchmark reproducibility locale is normalized to `C.UTF-8` so Windows locale strings do not break deterministic regression expectations.

Final verification:

- `uv run pytest tests/test_evaluator.py -q`: 10 passed.
- `uv run pytest -q`: 137 passed, 2 skipped, 6 warnings.
- `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_safety_invariants.py tests/test_legacy_import.py tests/test_workspace.py -q`: 32 passed, 1 skipped.
- `cd desktop && npm test`: 4 files, 8 tests passed.
- `cd desktop && npm run typecheck`: passed.
- `cd desktop && npm run build`: passed.
