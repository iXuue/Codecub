# CodeCub P0.2 Package Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Python backend identity from `pico` to `codecub`, including import path, CLI entry, module execution, default runtime data directory, user-facing text, README, and tests.

**Architecture:** P0.2 is a package and brand migration only. The current backend implementation is first copied into `codecub/`, references and tests are updated to the new package, and the old `pico/` source directory is removed only after separate deletion confirmation because the repository is dirty and the user requires explicit confirmation before deletion.

**Tech Stack:** Python 3.10+, setuptools via `pyproject.toml`, pytest, PowerShell, existing local backend modules.

---

## 1. Understood Requirement

P0.2 must satisfy the approved requirement document at `.codecub/spec/2026-06-11-codecub-p0-requirements.md`:

- Python package directory becomes `codecub/`.
- CLI entry point becomes `codecub`.
- `python -m codecub` works.
- Default project runtime data moves from `.pico/` to `.codecub/`.
- README and user-facing text use CodeCub naming.
- Tests and imports use `codecub`.
- Startup/welcome text no longer identifies as `pico`.

P0.2 does not implement:

- Electron UI.
- Real app approval bridge.
- `.pico/` legacy import prompt.
- Windows packaging executable.
- Terminal panel.
- Git status UI.

## 2. Current Repository Facts

- Current package directory: `pico/`.
- Current package metadata: `pyproject.toml` uses `name = "pico"`, script `pico = "pico.cli:main"`, packages `["pico"]`.
- P0.1 added `pico/app_protocol.py` and `pico/app_runner.py`.
- Runtime currently writes runs to `.pico/runs/`.
- CLI currently writes sessions to `.pico/sessions/`.
- Memory currently writes durable memory to `.pico/memory/`.
- `.gitignore` ignores `.pico/`, `pico.egg-info/`, and `docs/`.
- `.codecub/spec/` and `.codecub/plan/` must remain trackable.
- The worktree is dirty. Existing changes in `pico/context_manager.py`, `pico/memory.py`, `tests/test_context_manager.py`, and `tests/test_memory.py` are not part of P0.2 and must be preserved.

## 3. Confirmed Scope

Expected files or folders to create:

- `codecub/`
- `codecub/__init__.py`
- `codecub/__main__.py`
- `codecub/app_protocol.py`
- `codecub/app_runner.py`
- `codecub/cli.py`
- `codecub/context_manager.py`
- `codecub/evaluator.py`
- `codecub/memory.py`
- `codecub/metrics.py`
- `codecub/models.py`
- `codecub/run_store.py`
- `codecub/runtime.py`
- `codecub/task_state.py`
- `codecub/tools.py`
- `codecub/workspace.py`

Expected files to modify:

- `pyproject.toml`
- `.gitignore`
- `README.md`
- `tests/test_app_protocol.py`
- `tests/test_app_runner.py`
- `tests/test_context_manager.py`
- `tests/test_evaluator.py`
- `tests/test_memory.py`
- `tests/test_metrics.py`
- `tests/test_pico.py`
- `tests/test_run_store.py`
- `tests/test_safety_invariants.py`
- `tests/test_task_state.py`

Expected files or folders requiring separate deletion confirmation:

- `pico/`
- `pico.egg-info/`

`pico/` must not be deleted until after the new `codecub/` package imports, module execution, focused tests, and selected regression tests pass.

## 4. Backup Requirement

Before modifying or moving existing files, create:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-p0-2-package-migration"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath "pico" -Destination (Join-Path $backup "pico") -Recurse
Copy-Item -LiteralPath "tests" -Destination (Join-Path $backup "tests") -Recurse
Copy-Item -LiteralPath "pyproject.toml" -Destination (Join-Path $backup "pyproject.toml")
Copy-Item -LiteralPath ".gitignore" -Destination (Join-Path $backup ".gitignore")
Copy-Item -LiteralPath "README.md" -Destination (Join-Path $backup "README.md")
```

State the backup path before applying edits.

## 5. Implementation Tasks

### Task 1: Add Failing CodeCub Import And CLI Tests

**Files:**

- Modify: `tests/test_pico.py`
- Test: `tests/test_pico.py`

- [ ] **Step 1: Add package import test**

Append this test near the existing public API tests:

```python
def test_codecub_package_imports():
    import codecub
    from codecub.cli import build_arg_parser

    assert callable(codecub.main)
    assert callable(codecub.build_agent)
    assert callable(build_arg_parser)
    assert Path(codecub.__file__).as_posix().endswith("/codecub/__init__.py")
```

- [ ] **Step 2: Add module execution test**

Append this test near the existing module execution test:

```python
def test_codecub_module_execution_help_works():
    result = subprocess.run(
        [sys.executable, "-m", "codecub", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    assert "codecub" in result.stdout.lower() or "CodeCub" in result.stdout
    assert "--app-mode" in result.stdout
```

- [ ] **Step 3: Run tests and verify expected failure**

Run:

```powershell
uv run pytest tests/test_pico.py::test_codecub_package_imports tests/test_pico.py::test_codecub_module_execution_help_works -q
```

Expected result:

- Fails with `ModuleNotFoundError: No module named 'codecub'`.

### Task 2: Create `codecub/` Package From Current Backend

**Files:**

- Create: `codecub/`
- Read: `pico/`
- Test: `tests/test_pico.py`

- [ ] **Step 1: Copy package files**

Run after backup:

```powershell
Copy-Item -LiteralPath "pico" -Destination "codecub" -Recurse
```

Expected result:

- `codecub/__init__.py` exists.
- `codecub/__main__.py` exists.
- `codecub/app_protocol.py` exists.
- `codecub/app_runner.py` exists.

- [ ] **Step 2: Update `codecub/__init__.py` public names only if needed**

Keep the same public API names for compatibility inside the backend:

```python
from .cli import build_agent, build_arg_parser, build_welcome, main
from .models import AnthropicCompatibleModelClient, FakeModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .runtime import MiniAgent, Pico, SessionStore
from .workspace import WorkspaceContext

__all__ = [
    "AnthropicCompatibleModelClient",
    "FakeModelClient",
    "Pico",
    "build_agent",
    "build_arg_parser",
    "build_welcome",
    "main",
    "MiniAgent",
    "OllamaModelClient",
    "OpenAICompatibleModelClient",
    "SessionStore",
    "WorkspaceContext",
]
```

- [ ] **Step 3: Run import tests**

Run:

```powershell
uv run pytest tests/test_pico.py::test_codecub_package_imports -q
```

Expected result:

- Passes.

### Task 3: Update Package Metadata And Entrypoints

**Files:**

- Modify: `pyproject.toml`
- Test: module execution and script entry

- [ ] **Step 1: Change project metadata**

Replace the current project metadata with:

```toml
[project]
name = "codecub"
version = "0.1.0"
description = "CodeCub local desktop-first coding agent backend"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
codecub = "codecub.cli:main"
```

Replace setuptools packages with:

```toml
[tool.setuptools]
packages = ["codecub"]
```

- [ ] **Step 2: Run module help**

Run:

```powershell
uv run python -m codecub --help
```

Expected result:

- Exit code 0.
- Help includes `--app-mode`.

- [ ] **Step 3: Run console script help**

Run:

```powershell
uv run codecub --help
```

Expected result:

- Exit code 0.
- Help includes `--app-mode`.

### Task 4: Rename User-Facing Runtime Identity To CodeCub

**Files:**

- Modify: `codecub/cli.py`
- Modify: `codecub/runtime.py`
- Modify: `codecub/workspace.py`
- Test: `tests/test_pico.py`, `tests/test_context_manager.py`

- [ ] **Step 1: Update CLI constants and prompt**

In `codecub/cli.py`, set:

```python
WELCOME_NAME = "CodeCub"
WELCOME_TAGLINE = "local coding agent"
WELCOME_STATUS = "ready for repo work"
```

Update the REPL prompt:

```python
user_input = input("\ncodecub> ").strip()
```

- [ ] **Step 2: Update runtime system identity text**

In `codecub/runtime.py`, change the system prompt sentence from:

```text
You are pico, a small local coding agent working inside a local repository.
```

to:

```text
You are CodeCub, a local coding agent working inside a local repository.
```

- [ ] **Step 3: Update ignored path names**

In `codecub/workspace.py`, include `.codecub` in ignored path names:

```python
IGNORED_PATH_NAMES = {".git", ".pico", ".codecub", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}
```

Keep `.pico` ignored as legacy runtime state until P0.4 legacy import is implemented.

- [ ] **Step 4: Update focused tests**

Update tests that assert user-facing name or prompt:

```python
assert "CodeCub" in welcome
assert "codecub>" not in captured.out
assert prompt.index("You are CodeCub") < prompt.index("Memory:")
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_pico.py::test_welcome_screen_keeps_box_shape_for_long_paths tests/test_pico.py::test_main_dispatches_to_app_mode_without_welcome tests/test_context_manager.py::test_prompt_orders_prefix_memory_and_transcript -q
```

Expected result:

- Passes.

### Task 5: Change Default Runtime Data Directory To `.codecub/`

**Files:**

- Modify: `codecub/cli.py`
- Modify: `codecub/runtime.py`
- Modify: `codecub/memory.py`
- Modify: `codecub/evaluator.py`
- Modify: `codecub/metrics.py`
- Test: multiple backend tests

- [ ] **Step 1: Change CLI session store root**

In `codecub/cli.py`, change:

```python
store = SessionStore(workspace.repo_root + "/.pico/sessions")
```

to:

```python
store = SessionStore(workspace.repo_root + "/.codecub/sessions")
```

- [ ] **Step 2: Change runtime run store root**

In `codecub/runtime.py`, change:

```python
self.run_store = run_store or RunStore(Path(workspace.repo_root) / ".pico" / "runs")
```

to:

```python
self.run_store = run_store or RunStore(Path(workspace.repo_root) / ".codecub" / "runs")
```

- [ ] **Step 3: Change durable memory roots**

In `codecub/memory.py`, change default durable memory roots from:

```python
Path(workspace_root) / ".pico" / "memory"
```

to:

```python
Path(workspace_root) / ".codecub" / "memory"
```

- [ ] **Step 4: Change evaluator and metrics temporary stores**

In `codecub/evaluator.py` and `codecub/metrics.py`, change new session/run store paths from `.pico` to `.codecub`.

- [ ] **Step 5: Update tests from `.pico` to `.codecub`**

For new runtime artifacts, update test paths:

```python
tmp_path / ".codecub" / "sessions"
tmp_path / ".codecub" / "runs"
tmp_path / ".codecub" / "memory"
```

For text facts that intentionally describe legacy import behavior, keep `.pico/` only if the assertion is explicitly about legacy data.

- [ ] **Step 6: Run runtime storage tests**

Run:

```powershell
uv run pytest tests/test_run_store.py tests/test_memory.py tests/test_pico.py::test_successful_run_persists_run_artifacts_and_stop_reason tests/test_pico.py::test_explicit_memory_promotion_persists_durable_memory_topics -q
```

Expected result:

- Passes.
- New artifacts are under `.codecub/`.

### Task 6: Update Test Imports And Patch Targets To `codecub`

**Files:**

- Modify: all `tests/test_*.py` files importing or patching `pico`
- Test: selected backend suite

- [ ] **Step 1: Replace import statements**

Replace examples like:

```python
from pico import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from pico.runtime import Pico
import pico as mini_pkg
```

with:

```python
from codecub import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from codecub.runtime import Pico
import codecub as mini_pkg
```

- [ ] **Step 2: Replace patch targets**

Replace patch targets like:

```python
patch("pico.cli.OllamaModelClient")
patch("pico.tools.subprocess.run")
patch("pico.tools.tool_delegate")
```

with:

```python
patch("codecub.cli.OllamaModelClient")
patch("codecub.tools.subprocess.run")
patch("codecub.tools.tool_delegate")
```

- [ ] **Step 3: Update module assertions**

Replace assertions like:

```python
assert agent.tool_run_shell.__func__.__module__ == "pico.runtime"
assert Path(mini_pkg.__file__).as_posix().endswith("/pico/__init__.py")
```

with:

```python
assert agent.tool_run_shell.__func__.__module__ == "codecub.runtime"
assert Path(mini_pkg.__file__).as_posix().endswith("/codecub/__init__.py")
```

- [ ] **Step 4: Run selected import suite**

Run:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_task_state.py tests/test_run_store.py tests/test_safety_invariants.py -q
```

Expected result:

- Passes, except symlink test may skip on Windows without symlink privilege.

### Task 7: Update README And `.gitignore`

**Files:**

- Modify: `README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Update README identity**

Replace the README heading and core launch commands:

```markdown
# CodeCub

`CodeCub` 是一个本地优先的 coding agent 后端，P0 阶段正在迁移为桌面端 CodeCub 的后端能力层。
```

Use these command examples:

```markdown
uv run codecub
uv run codecub --cwd /path/to/repo
uv run codecub "inspect the test failures and propose a fix"
python -m codecub
```

- [ ] **Step 2: Update README runtime data paths**

Document new paths:

```markdown
- 会话保存在 `.codecub/sessions/`
- 每次运行的工件保存在 `.codecub/runs/<run_id>/`
- 长期记忆保存在 `.codecub/memory/`
```

Document legacy boundary:

```markdown
旧版 `.pico/` 数据不会在 P0.2 自动导入；导入提示和复制流程属于 P0.4。
```

- [ ] **Step 3: Update `.gitignore`**

Keep `.pico/` ignored and replace `pico.egg-info/` with both old and new package metadata ignores:

```gitignore
.pico/
.codecub/sessions/
.codecub/runs/
.codecub/memory/
.codecub/cache/
.codecub/logs/
pico.egg-info/
codecub.egg-info/
```

Do not add:

```gitignore
.codecub/
```

- [ ] **Step 4: Verify planning docs remain visible**

Run:

```powershell
git status --short .codecub
```

Expected result:

- `.codecub/spec/` and `.codecub/plan/` files remain visible as untracked or modified files.

### Task 8: Run Selected Regression And Review Old Package Removal

**Files:**

- Read: `pico/`
- Read: `codecub/`
- Test: selected regression suite

- [ ] **Step 1: Run selected regression**

Run:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py tests/test_context_manager.py tests/test_memory.py tests/test_run_store.py tests/test_task_state.py -q
```

Expected result:

- Passes, except environment-conditioned skips already classified in P0.1.

- [ ] **Step 2: Search for remaining product/package references**

Run:

```powershell
rg -n "\bpico\b|\.pico|python -m pico|uv run pico|pico>" README.md pyproject.toml codecub tests .gitignore -S
```

Expected result:

- No remaining references in user-facing or active package paths, except legacy `.pico/` mentions that explicitly describe import boundaries or ignored legacy data.

- [ ] **Step 3: Stop for deletion confirmation**

Before deleting old source or generated package metadata, report:

```text
需要删除：
- D:\代码备份\pico\pico-main\pico
- D:\代码备份\pico\pico-main\pico.egg-info

原因：P0.2 要完成包名迁移，旧源码目录和旧 egg-info 会造成双包并存。
可恢复性：已备份到本次 P0.2 执行开始时报告的 E:\codex_backup\yyyyMMdd-HHmmss-codecub-p0-2-package-migration 目录，删除后也可从备份恢复。
```

Wait for explicit user confirmation before deleting.

## 6. Verification Commands

Focused verification:

```powershell
uv run pytest tests/test_pico.py::test_codecub_package_imports tests/test_pico.py::test_codecub_module_execution_help_works -q
uv run python -m codecub --help
uv run codecub --help
```

Selected regression:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py tests/test_context_manager.py tests/test_memory.py tests/test_run_store.py tests/test_task_state.py -q
```

Reference scan:

```powershell
rg -n "\bpico\b|\.pico|python -m pico|uv run pico|pico>" README.md pyproject.toml codecub tests .gitignore -S
```

## 7. Known Risks And Stop Conditions

- Stop if copying `pico/` to `codecub/` would overwrite an existing `codecub/` directory.
- Stop if tests reveal imports still using `pico` after pyproject switches to `codecub`.
- Stop if `.codecub/spec/` or `.codecub/plan/` become ignored by Git.
- Stop before deleting `pico/` or `pico.egg-info/`; deletion requires explicit confirmation.
- Stop if P0.2 changes require modifying files outside `D:\代码备份\pico\pico-main`.
- Stop if new package migration conflicts with existing uncommitted memory/context changes.

## 8. Plan Review

### Requirement Coverage

- Package directory: covered by Task 2.
- CLI entry point: covered by Task 3.
- `python -m codecub`: covered by Task 1 and Task 3.
- Default `.codecub/` runtime data: covered by Task 5.
- README and user-facing docs: covered by Task 7.
- Tests and imports: covered by Task 6.
- Startup/welcome text: covered by Task 4.
- Dirty worktree protection and backups: covered by Section 4 and stop conditions.

### Unresolved Questions

- Whether to physically delete `pico/` during P0.2 is intentionally unresolved until after tests pass, because deletion needs explicit confirmation.
- Whether to remove `pico.egg-info/` is intentionally unresolved until deletion confirmation, because it is an existing generated directory.

### Maintenance Review

The plan avoids a broad behavioral refactor. It keeps P0.2 focused on naming, packaging, and default data paths. Legacy `.pico/` import remains deferred to P0.4 as required by the master plan, while `.pico/` stays ignored to avoid accidentally tracking legacy local runtime state.

## 9. P0.2 Execution Status

- Started: 2026-06-12 15:11 Asia/Shanghai.
- Completed: package migration and old package deletion verified on 2026-06-12 16:48 Asia/Shanghai.
- Primary backup path:
  - `E:\codex_backup\20260612-151105-codecub-p0-2-package-migration`
- Plan status backup path:
  - `E:\codex_backup\20260612-163711-codecub-p0-2-plan-execution-status`
  - `E:\codex_backup\20260612-164837-codecub-p0-2-plan-delete-status`
- Old package deletion backup path:
  - `E:\codex_backup\20260612-164729-codecub-p0-2-delete-old-pico`
- Files created:
  - `codecub/`
- Files modified:
  - `pyproject.toml`
  - `.gitignore`
  - `README.md`
  - `tests/test_app_protocol.py`
  - `tests/test_app_runner.py`
  - `tests/test_context_manager.py`
  - `tests/test_evaluator.py`
  - `tests/test_memory.py`
  - `tests/test_metrics.py`
  - `tests/test_pico.py`
  - `tests/test_run_store.py`
  - `tests/test_safety_invariants.py`
  - `tests/test_task_state.py`
- Important implementation note:
  - `pico/` and `pico.egg-info/` were deleted after explicit user confirmation.
  - The new `codecub/` package was copied from the current backend and migrated in place.
  - Some existing test/source files contain historical non-UTF-8 or mojibake text. PowerShell text rewriting corrupted two files during execution; they were restored from backup and migrated with byte-level ASCII replacements to avoid changing those non-ASCII byte sequences.
- Tests run:
  - `uv run python -m py_compile <codecub *.py and tests/test_*.py>`
  - `uv run pytest tests/test_pico.py::test_public_api_exports_resolve_through_package_path tests/test_pico.py::test_module_execution_help_works -q`
  - `uv run python -m codecub --help`
  - `uv run codecub --help`
  - `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_task_state.py tests/test_run_store.py tests/test_safety_invariants.py -q`
  - `uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py tests/test_context_manager.py tests/test_memory.py tests/test_run_store.py tests/test_task_state.py -q`
- Passing results:
  - Focused package/help tests: 2 passed.
  - Import/storage/app-mode subset: 31 passed, 1 skipped.
  - Selected regression: 112 passed, 2 skipped.
  - After deleting `pico/` and `pico.egg-info/`, selected regression still passed: 112 passed, 2 skipped.
  - `python -m codecub --help` and `uv run codecub --help` both show CodeCub help and `--app-mode`.
- Deletion confirmation result:
  - `D:\代码备份\pico\pico-main\pico` deleted.
  - `D:\代码备份\pico\pico-main\pico.egg-info` deleted.
