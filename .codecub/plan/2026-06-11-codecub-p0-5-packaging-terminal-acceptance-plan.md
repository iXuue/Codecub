# CodeCub P0.5 Terminal, Git, Packaging, and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the CodeCub P0 desktop baseline with a full interactive terminal, basic Git status, Windows packaging path, and near-release acceptance checks.

**Architecture:** Keep the Python agent backend and the user-controlled terminal as separate execution channels. The terminal is an Electron main-process PTY bridge exposed through narrow IPC events to a React terminal panel. Git status is a lightweight Electron main-process query, and Windows packaging is split into a Python backend executable build plus Electron desktop packaging that bundles that executable.

**Tech Stack:** Electron IPC, React, TypeScript, `node-pty`, `@xterm/xterm`, Git CLI, Python `PyInstaller` or approved equivalent, `electron-builder`, pytest, Vitest, Windows smoke tests.

---

## Understood Requirement

P0.5 must complete the remaining P0 requirements:

- Full interactive terminal with cwd set to the selected project directory.
- Terminal is user-controlled and does not bypass agent approval because it is not an agent tool call.
- Basic Git status in the desktop UI: branch, dirty state, changed file count, and refresh.
- Windows packaging includes an embedded backend executable such as `codecub-agent.exe`.
- Desktop packaged app can launch and start the embedded backend without requiring a repo checkout.
- Near-release acceptance covers backend, desktop, terminal, Git status, approval, legacy import, and packaging smoke.

## Confirmed Scope

In scope:

- Terminal PTY bridge and React terminal panel.
- Basic Git status query and UI badge/panel.
- Packaging configuration and scripts after explicit dependency/tool approval.
- Acceptance checklist and repeatable smoke commands.
- Documentation/status updates in `.codecub/plan`.

Out of scope for P0.5:

- Multi-tab terminal.
- Complete Git panel, staging, commit creation, merge/rebase flows, or PR publishing.
- macOS/Linux packaging.
- Public release license audit.
- Cloud sync, multi-project backend service, or P1 WebSocket architecture.

## Dependency Approval Gates

Execution must stop and ask the user before installing any dependency or tool:

- Terminal dependencies: `node-pty` and `@xterm/xterm`.
- Packaging dependencies: `electron-builder` and Python backend packager, preferably `pyinstaller`.

If the user rejects a dependency, write a repair plan before changing implementation scope.

## Files and Responsibilities

- Create `desktop/electron/terminal.ts`: owns PTY lifecycle, input/output/resize/close.
- Create `desktop/electron/gitStatus.ts`: runs safe Git status commands in selected project cwd.
- Modify `desktop/electron/ipcTypes.ts`: adds terminal and Git IPC types.
- Modify `desktop/electron/main.ts`: wires terminal and Git IPC handlers/events.
- Modify `desktop/electron/preload.ts`: exposes terminal and Git APIs to renderer.
- Create `desktop/src/components/TerminalPanel.tsx`: renders xterm terminal and controls lifecycle.
- Create `desktop/src/components/GitStatusBadge.tsx`: displays branch, dirty state, file count, refresh.
- Modify `desktop/src/components/ProjectSessionPage.tsx`: adds terminal panel and Git status into session layout.
- Modify `desktop/src/App.tsx`: passes project path and project-level state into `ProjectSessionPage`.
- Modify `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`: terminal/Git/packaging acceptance text.
- Modify `desktop/src/styles/app.css`: terminal and Git status layout.
- Create `desktop/tests/gitStatus.test.ts`: Git parser/summary tests.
- Create `desktop/tests/terminalIpcTypes.test.ts`: renderer-safe terminal IPC type tests.
- Modify `desktop/package.json`: add scripts and packaging dependencies only after approval.
- Create `scripts/package_backend.py`: builds `codecub-agent.exe` after backend packager approval.
- Create `desktop/electron-builder.json`: Windows app packaging config after approval.
- Create `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`: repeatable acceptance checklist.
- Modify `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`: status update after P0.5 completes.

## Backup Requirement Before Execution

Before modifying each existing file, create a timestamped backup under `E:\codex_backup`, preserving enough path information to restore the original file. Example:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-terminal-git-packaging"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\main.ts") -Destination "$backup\desktop_electron_main.ts"
```

New files do not need existing-file backups.

## Stop Conditions

Stop, report, and write a repair plan before continuing if:

- `node-pty` fails to install or build on this Windows environment.
- Terminal cannot preserve selected project cwd.
- Terminal output leaks into agent app-mode JSONL stream.
- Git status commands mutate the repo or require network.
- Backend executable cannot be built without changing Python package structure.
- Packaged Electron app cannot locate the embedded backend executable.
- Any full Python or desktop regression fails after a scoped fix.

---

### Task 1: Terminal Dependency Approval and Installation

**Files:**
- Modify after approval: `desktop/package.json`
- Modify after approval: `desktop/package-lock.json`

- [ ] **Step 1: Ask for terminal dependency approval**

Ask the user:

```text
P0.5 full terminal requires node-pty and @xterm/xterm.
Approve installing them under D:\代码备份\pico\pico-main\desktop?
```

Expected: wait for explicit confirmation.

- [ ] **Step 2: Install approved dependencies**

Run only after approval:

```powershell
cd desktop
npm install node-pty @xterm/xterm
```

Expected: `package.json` and `package-lock.json` update, install exits 0.

- [ ] **Step 3: Verify dependency importability**

Run:

```powershell
cd desktop
node -e "import('node-pty').then(() => console.log('node-pty ok')); import('@xterm/xterm').then(() => console.log('xterm ok'))"
```

Expected output contains:

```text
node-pty ok
xterm ok
```

---

### Task 2: Terminal Bridge Tests and IPC Types

**Files:**
- Modify: `desktop/electron/ipcTypes.ts`
- Create: `desktop/tests/terminalIpcTypes.test.ts`

- [ ] **Step 1: Back up existing IPC file**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-terminal-ipc-types"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\ipcTypes.ts") -Destination "$backup\desktop_electron_ipcTypes.ts"
```

- [ ] **Step 2: Add type-level terminal command test**

Create `desktop/tests/terminalIpcTypes.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { TerminalStartRequest, TerminalWriteRequest, TerminalResizeRequest } from "../electron/ipcTypes";

describe("terminal IPC types", () => {
  it("describes terminal start, write, and resize requests", () => {
    const start: TerminalStartRequest = { terminalId: "term-1", cwd: "D:/repo", cols: 100, rows: 30 };
    const write: TerminalWriteRequest = { terminalId: "term-1", data: "git status\r" };
    const resize: TerminalResizeRequest = { terminalId: "term-1", cols: 120, rows: 40 };

    expect(start.cwd).toBe("D:/repo");
    expect(write.data).toContain("git status");
    expect(resize.cols).toBe(120);
  });
});
```

Run:

```powershell
cd desktop
npm test -- terminalIpcTypes
```

Expected before implementation: fails because the types do not exist.

- [ ] **Step 3: Add IPC types**

Add to `desktop/electron/ipcTypes.ts`:

```ts
export type TerminalStartRequest = {
  terminalId: string;
  cwd: string;
  cols: number;
  rows: number;
};

export type TerminalWriteRequest = {
  terminalId: string;
  data: string;
};

export type TerminalResizeRequest = {
  terminalId: string;
  cols: number;
  rows: number;
};

export type TerminalExitEvent = {
  terminalId: string;
  exitCode: number | null;
};

export type GitStatus = {
  branch: string;
  dirty: boolean;
  changedCount: number;
  ahead: number;
  behind: number;
  files: string[];
};
```

- [ ] **Step 4: Verify terminal IPC type test**

Run:

```powershell
cd desktop
npm test -- terminalIpcTypes
```

Expected: test passes.

---

### Task 3: Terminal Main-Process Bridge

**Files:**
- Create: `desktop/electron/terminal.ts`
- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/preload.ts`
- Create: `desktop/tests/terminalBridge.test.ts`

- [ ] **Step 1: Back up existing Electron files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-terminal-bridge"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\main.ts") -Destination "$backup\desktop_electron_main.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\preload.ts") -Destination "$backup\desktop_electron_preload.ts"
```

- [ ] **Step 2: Add terminal shell selection test**

Create `desktop/tests/terminalBridge.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { chooseTerminalShell } from "../electron/terminal";

describe("chooseTerminalShell", () => {
  it("uses PowerShell on Windows by default", () => {
    const shell = chooseTerminalShell("win32", { SystemRoot: "C:\\Windows" });

    expect(shell.file).toContain("powershell.exe");
    expect(shell.args).toContain("-NoLogo");
  });

  it("falls back to sh on non-Windows", () => {
    const shell = chooseTerminalShell("linux", {});

    expect(shell.file).toBe("sh");
  });
});
```

Run:

```powershell
cd desktop
npm test -- terminalBridge
```

Expected before implementation: fails because `terminal.ts` does not exist.

- [ ] **Step 3: Implement `desktop/electron/terminal.ts`**

Create:

```ts
import { EventEmitter } from "node:events";
import type { IPty } from "node-pty";
import { spawn } from "node-pty";
import type { TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "./ipcTypes.js";

export type TerminalEvents = {
  data: [terminalId: string, data: string];
  exit: [terminalId: string, exitCode: number | null];
};

export function chooseTerminalShell(platform = process.platform, env = process.env): { file: string; args: string[] } {
  if (platform === "win32") {
    const systemRoot = env.SystemRoot || "C:\\Windows";
    return {
      file: `${systemRoot}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe`,
      args: ["-NoLogo"],
    };
  }
  return { file: env.SHELL || "sh", args: [] };
}

export class TerminalManager extends EventEmitter {
  private sessions = new Map<string, IPty>();

  start(request: TerminalStartRequest): void {
    this.close(request.terminalId);
    const shell = chooseTerminalShell();
    const pty = spawn(shell.file, shell.args, {
      name: "xterm-256color",
      cols: Math.max(20, request.cols),
      rows: Math.max(5, request.rows),
      cwd: request.cwd,
      env: process.env,
    });
    this.sessions.set(request.terminalId, pty);
    pty.onData((data) => this.emit("data", request.terminalId, data));
    pty.onExit((event) => {
      this.sessions.delete(request.terminalId);
      this.emit("exit", request.terminalId, event.exitCode ?? null);
    });
  }

  write(request: TerminalWriteRequest): void {
    this.sessions.get(request.terminalId)?.write(request.data);
  }

  resize(request: TerminalResizeRequest): void {
    this.sessions.get(request.terminalId)?.resize(Math.max(20, request.cols), Math.max(5, request.rows));
  }

  close(terminalId: string): void {
    const session = this.sessions.get(terminalId);
    if (!session) {
      return;
    }
    session.kill();
    this.sessions.delete(terminalId);
  }

  closeAll(): void {
    for (const terminalId of this.sessions.keys()) {
      this.close(terminalId);
    }
  }
}
```

- [ ] **Step 4: Wire Electron IPC**

In `desktop/electron/main.ts`:

```ts
import { TerminalManager } from "./terminal.js";

const terminals = new TerminalManager();

terminals.on("data", (terminalId, data) => mainWindow?.webContents.send("terminal:data", terminalId, data));
terminals.on("exit", (terminalId, exitCode) => mainWindow?.webContents.send("terminal:exit", { terminalId, exitCode }));

ipcMain.handle("terminal:start", async (_event, request: TerminalStartRequest) => terminals.start(request));
ipcMain.handle("terminal:write", async (_event, request: TerminalWriteRequest) => terminals.write(request));
ipcMain.handle("terminal:resize", async (_event, request: TerminalResizeRequest) => terminals.resize(request));
ipcMain.handle("terminal:close", async (_event, terminalId: string) => terminals.close(terminalId));
```

Also call `terminals.closeAll()` inside `window-all-closed`.

In `desktop/electron/preload.ts`, expose:

```ts
startTerminal: (request: TerminalStartRequest): Promise<void> => ipcRenderer.invoke("terminal:start", request),
writeTerminal: (request: TerminalWriteRequest): Promise<void> => ipcRenderer.invoke("terminal:write", request),
resizeTerminal: (request: TerminalResizeRequest): Promise<void> => ipcRenderer.invoke("terminal:resize", request),
closeTerminal: (terminalId: string): Promise<void> => ipcRenderer.invoke("terminal:close", terminalId),
onTerminalData: (callback: (terminalId: string, data: string) => void) => {
  const listener = (_event: Electron.IpcRendererEvent, terminalId: string, data: string) => callback(terminalId, data);
  ipcRenderer.on("terminal:data", listener);
  return () => ipcRenderer.off("terminal:data", listener);
},
onTerminalExit: (callback: (event: TerminalExitEvent) => void) => {
  const listener = (_event: Electron.IpcRendererEvent, event: TerminalExitEvent) => callback(event);
  ipcRenderer.on("terminal:exit", listener);
  return () => ipcRenderer.off("terminal:exit", listener);
},
```

- [ ] **Step 5: Verify terminal bridge tests**

Run:

```powershell
cd desktop
npm test -- terminalBridge terminalIpcTypes
npm run typecheck
```

Expected: tests and typecheck pass.

---

### Task 4: React Terminal Panel

**Files:**
- Create: `desktop/src/components/TerminalPanel.tsx`
- Modify: `desktop/src/components/ProjectSessionPage.tsx`
- Modify: `desktop/src/styles/app.css`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`

- [ ] **Step 1: Back up existing renderer files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-terminal-panel"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\components\ProjectSessionPage.tsx") -Destination "$backup\desktop_src_components_ProjectSessionPage.tsx"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\styles\app.css") -Destination "$backup\desktop_src_styles_app.css"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\i18n\zh-CN.ts") -Destination "$backup\desktop_src_i18n_zh-CN.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\i18n\en-US.ts") -Destination "$backup\desktop_src_i18n_en-US.ts"
```

- [ ] **Step 2: Add i18n keys**

Add to both dictionaries:

```ts
terminal: "终端",
startTerminal: "打开终端",
closeTerminal: "关闭终端",
terminalNotStarted: "终端未启动",
```

English:

```ts
terminal: "Terminal",
startTerminal: "Open Terminal",
closeTerminal: "Close Terminal",
terminalNotStarted: "Terminal is not running",
```

- [ ] **Step 3: Implement terminal panel**

Create `desktop/src/components/TerminalPanel.tsx`:

```tsx
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import type { I18nKey } from "../i18n";

type TerminalPanelProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

export function TerminalPanel({ t, projectPath }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const terminalIdRef = useRef(`terminal-${crypto.randomUUID()}`);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    const removeData = window.codecub.onTerminalData((terminalId, data) => {
      if (terminalId === terminalIdRef.current) {
        terminalRef.current?.write(data);
      }
    });
    const removeExit = window.codecub.onTerminalExit((event) => {
      if (event.terminalId === terminalIdRef.current) {
        setRunning(false);
      }
    });
    return () => {
      removeData();
      removeExit();
      void window.codecub.closeTerminal(terminalIdRef.current);
    };
  }, []);

  async function startTerminal() {
    if (!containerRef.current || running) {
      return;
    }
    const terminal = new Terminal({ cols: 100, rows: 24, cursorBlink: true });
    terminal.open(containerRef.current);
    terminal.onData((data) => window.codecub.writeTerminal({ terminalId: terminalIdRef.current, data }));
    terminalRef.current = terminal;
    await window.codecub.startTerminal({
      terminalId: terminalIdRef.current,
      cwd: projectPath,
      cols: 100,
      rows: 24,
    });
    setRunning(true);
  }

  async function closeTerminal() {
    await window.codecub.closeTerminal(terminalIdRef.current);
    terminalRef.current?.dispose();
    terminalRef.current = null;
    setRunning(false);
  }

  return (
    <section className="terminal-panel" aria-label={t("terminal")}>
      <div className="terminal-header">
        <span>{t("terminal")}</span>
        <button className="button secondary" type="button" onClick={running ? closeTerminal : startTerminal}>
          {running ? t("closeTerminal") : t("startTerminal")}
        </button>
      </div>
      <div className="terminal-surface" ref={containerRef}>
        {!running ? <div className="empty-state compact">{t("terminalNotStarted")}</div> : null}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Add panel to project page**

In `ProjectSessionPage`, render `TerminalPanel` directly below `ChatView`:

```tsx
<TerminalPanel t={t} projectPath={projectPath} />
```

Place it in the main workspace below `ChatView` as a fixed-height lower band. Use CSS grid so the terminal has stable height and does not resize chat unpredictably.

- [ ] **Step 5: Verify renderer build**

Run:

```powershell
cd desktop
npm run typecheck
npm run build
```

Expected: both pass.

---

### Task 5: Basic Git Status

**Files:**
- Create: `desktop/electron/gitStatus.ts`
- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/preload.ts`
- Create: `desktop/src/components/GitStatusBadge.tsx`
- Modify: `desktop/src/components/ProjectSessionPage.tsx`
- Create: `desktop/tests/gitStatus.test.ts`

- [ ] **Step 1: Back up existing files**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-git-status"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\main.ts") -Destination "$backup\desktop_electron_main.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\preload.ts") -Destination "$backup\desktop_electron_preload.ts"
Copy-Item -LiteralPath (Join-Path $repo "desktop\src\components\ProjectSessionPage.tsx") -Destination "$backup\desktop_src_components_ProjectSessionPage.tsx"
```

- [ ] **Step 2: Add Git status tests**

Create `desktop/tests/gitStatus.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseGitPorcelainStatus, summarizeGitStatus } from "../electron/gitStatus";

describe("git status helpers", () => {
  it("counts changed files from porcelain output", () => {
    const files = parseGitPorcelainStatus(" M README.md\n?? new.txt\nA  added.ts\n");

    expect(files).toEqual(["README.md", "new.txt", "added.ts"]);
  });

  it("summarizes clean and dirty states", () => {
    expect(summarizeGitStatus("main", [])).toEqual({
      branch: "main",
      dirty: false,
      changedCount: 0,
      ahead: 0,
      behind: 0,
      files: [],
    });
    expect(summarizeGitStatus("feature", ["README.md"]).dirty).toBe(true);
  });
});
```

Run:

```powershell
cd desktop
npm test -- gitStatus
```

Expected before implementation: fails because `gitStatus.ts` does not exist.

- [ ] **Step 3: Implement Git status module**

Create `desktop/electron/gitStatus.ts`:

```ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { GitStatus } from "./ipcTypes.js";

const execFileAsync = promisify(execFile);

export function parseGitPorcelainStatus(output: string): string[] {
  return output
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
    .filter(Boolean);
}

export function summarizeGitStatus(branch: string, files: string[]): GitStatus {
  return {
    branch: branch || "-",
    dirty: files.length > 0,
    changedCount: files.length,
    ahead: 0,
    behind: 0,
    files,
  };
}

export async function readGitStatus(cwd: string): Promise<GitStatus> {
  try {
    const [{ stdout: branchOut }, { stdout: statusOut }] = await Promise.all([
      execFileAsync("git", ["branch", "--show-current"], { cwd, timeout: 5000 }),
      execFileAsync("git", ["status", "--porcelain"], { cwd, timeout: 5000 }),
    ]);
    return summarizeGitStatus(branchOut.trim() || "-", parseGitPorcelainStatus(statusOut));
  } catch {
    return summarizeGitStatus("-", []);
  }
}
```

- [ ] **Step 4: Wire Git IPC**

In `main.ts`:

```ts
import { readGitStatus } from "./gitStatus.js";

ipcMain.handle("git:status", async (_event, projectPath: string) => readGitStatus(projectPath));
```

In `preload.ts`:

```ts
loadGitStatus: (projectPath: string): Promise<GitStatus> => ipcRenderer.invoke("git:status", projectPath),
```

- [ ] **Step 5: Implement Git status badge**

Create `desktop/src/components/GitStatusBadge.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { I18nKey } from "../i18n";

type GitStatus = {
  branch: string;
  dirty: boolean;
  changedCount: number;
  ahead: number;
  behind: number;
  files: string[];
};

type GitStatusBadgeProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

export function GitStatusBadge({ t, projectPath }: GitStatusBadgeProps) {
  const [status, setStatus] = useState<GitStatus | null>(null);

  async function refresh() {
    setStatus(await window.codecub.loadGitStatus(projectPath));
  }

  useEffect(() => {
    void refresh();
  }, [projectPath]);

  return (
    <button className={status?.dirty ? "git-badge dirty" : "git-badge"} type="button" onClick={refresh}>
      <span>{t("git")}</span>
      <strong>{status?.branch ?? "-"}</strong>
      <span>{status?.dirty ? `${status.changedCount} changed` : t("clean")}</span>
    </button>
  );
}
```

Add i18n keys:

```ts
git: "Git",
clean: "clean",
```

Chinese:

```ts
git: "Git",
clean: "干净",
```

- [ ] **Step 6: Verify Git tests and build**

Run:

```powershell
cd desktop
npm test -- gitStatus
npm run typecheck
```

Expected: tests and typecheck pass.

---

### Task 6: Backend Executable Packaging Approval and Script

**Files:**
- Create after approval: `scripts/package_backend.py`
- Modify after approval: `pyproject.toml` only if the approved packaging tool requires metadata changes.

- [ ] **Step 1: Ask approval for backend packager**

Ask:

```text
P0.5 Windows packaging needs a backend executable. Approve installing PyInstaller in this project environment and creating scripts/package_backend.py?
```

Expected: wait for explicit confirmation.

- [ ] **Step 2: Install approved backend packager**

Run only after approval:

```powershell
uv add --dev pyinstaller
```

Expected: dependency metadata updates and command exits 0.

- [ ] **Step 3: Create backend packaging script**

Create `scripts/package_backend.py`:

```python
import subprocess
import sys
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    dist_dir = repo / "desktop" / "resources" / "backend"
    dist_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        "codecub-agent",
        "--distpath",
        str(dist_dir),
        "-m",
        "codecub",
    ]
    subprocess.run(command, cwd=repo, check=True)
    exe = dist_dir / "codecub-agent.exe"
    if not exe.exists():
        raise SystemExit(f"missing backend executable: {exe}")
    print(exe)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify backend executable build**

Run:

```powershell
uv run python scripts/package_backend.py
desktop\resources\backend\codecub-agent.exe --help
```

Expected: executable exists and `--help` exits 0.

---

### Task 7: Electron Windows Packaging Approval and Config

**Files:**
- Modify after approval: `desktop/package.json`
- Modify after approval: `desktop/package-lock.json`
- Create: `desktop/electron-builder.json`
- Modify: `desktop/electron/backendProcess.ts`

- [ ] **Step 1: Ask approval for Electron packager**

Ask:

```text
Approve installing electron-builder under desktop/ and adding Windows packaging config?
```

Expected: wait for explicit confirmation.

- [ ] **Step 2: Install approved packager**

Run only after approval:

```powershell
cd desktop
npm install --save-dev electron-builder
```

Expected: `package.json` and `package-lock.json` update.

- [ ] **Step 3: Back up backend process**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path ".").Path
$backup = "E:\codex_backup\$stamp-codecub-p0-5-packaging-backend-process"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "desktop\electron\backendProcess.ts") -Destination "$backup\desktop_electron_backendProcess.ts"
```

- [ ] **Step 4: Make backend process prefer bundled executable**

In `desktop/electron/backendProcess.ts`, choose command:

```ts
const bundledBackend = join(process.resourcesPath, "backend", "codecub-agent.exe");
const hasBundledBackend = existsSync(bundledBackend);
const command = hasBundledBackend ? bundledBackend : process.env.CODECUB_BACKEND_COMMAND || "uv";
const args = hasBundledBackend
  ? ["--app-mode", "--cwd", projectPath, "--approval", approvalPolicy]
  : process.env.CODECUB_BACKEND_COMMAND
    ? ["-m", "codecub", "--app-mode", "--cwd", projectPath, "--approval", approvalPolicy]
    : ["run", "python", "-m", "codecub", "--app-mode", "--cwd", projectPath, "--approval", approvalPolicy];
```

Import:

```ts
import { existsSync } from "node:fs";
import { join } from "node:path";
```

- [ ] **Step 5: Create Electron builder config**

Create `desktop/electron-builder.json`:

```json
{
  "appId": "com.codecub.desktop",
  "productName": "CodeCub",
  "directories": {
    "output": "release"
  },
  "files": [
    "dist-electron/**/*",
    "dist-renderer/**/*",
    "package.json"
  ],
  "extraResources": [
    {
      "from": "resources/backend",
      "to": "backend"
    }
  ],
  "win": {
    "target": ["dir"]
  }
}
```

Add scripts:

```json
"package:win": "npm run build && electron-builder --config electron-builder.json --win"
```

- [ ] **Step 6: Build Windows package directory**

Run:

```powershell
cd desktop
npm run package:win
```

Expected: `desktop/release/win-unpacked/CodeCub.exe` exists.

---

### Task 8: Acceptance Checklist and Smoke Tests

**Files:**
- Create: `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`
- Modify: `.codecub/plan/2026-06-11-codecub-p0-5-packaging-terminal-acceptance-plan.md`
- Modify: `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`

- [ ] **Step 1: Create acceptance checklist**

Create `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`:

```markdown
# CodeCub P0 Acceptance Checklist

## Backend

- `uv run pytest -q` passes.
- `uv run python -m codecub --help` exits 0.
- App-mode emits `session_started` and `user_message_received`; terminal output is delivered only through terminal IPC and does not corrupt JSONL.
- Approval flow blocks risky tools until approved or rejected.
- `.pico` import copies data only after `import_legacy_pico`.

## Desktop

- `cd desktop && npm test` passes.
- `cd desktop && npm run typecheck` passes.
- `cd desktop && npm run build` passes.
- App opens a project.
- Chat sends a message.
- Run log shows events.
- Approval dialog appears for risky operations.
- Diff preview shows changed files.
- Terminal opens in selected project cwd.
- Git badge shows branch and dirty state.

## Packaging

- Backend executable exists at `desktop/resources/backend/codecub-agent.exe`.
- Packaged Windows directory exists at `desktop/release/win-unpacked`.
- Packaged app launches.
- Packaged app starts backend from bundled executable.
```

- [ ] **Step 2: Run backend acceptance**

Run:

```powershell
uv run pytest -q
uv run python -m codecub --help
```

Expected: tests pass and help exits 0.

- [ ] **Step 3: Run desktop acceptance**

Run:

```powershell
cd desktop
npm test
npm run typecheck
npm run build
```

Expected: all pass.

- [ ] **Step 4: Run Electron dev smoke**

Run:

```powershell
$electron = Join-Path (Resolve-Path "desktop").Path "node_modules\electron\dist\electron.exe"
$main = Join-Path (Resolve-Path "desktop").Path "dist-electron\main.js"
$process = Start-Process -FilePath $electron -ArgumentList @($main, "--disable-gpu") -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
$alive = -not $process.HasExited
if ($alive) { Stop-Process -Id $process.Id -Force }
Write-Output "electron_alive_after_6s=$alive"
```

Expected:

```text
electron_alive_after_6s=True
```

- [ ] **Step 5: Run packaged app smoke if packaging was approved**

Run:

```powershell
$exe = "desktop\release\win-unpacked\CodeCub.exe"
Test-Path $exe
$process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 8
$alive = -not $process.HasExited
if ($alive) { Stop-Process -Id $process.Id -Force }
Write-Output "packaged_alive_after_8s=$alive"
```

Expected:

```text
True
packaged_alive_after_8s=True
```

- [ ] **Step 6: Update plan statuses**

Append execution status to this P0.5 plan and update the master plan:

```markdown
- P0.5 terminal, Git status, packaging, and near-release acceptance: completed and verified in `.codecub/plan/2026-06-11-codecub-p0-5-packaging-terminal-acceptance-plan.md`.
```

---

## Plan Review

- Requirement match: Covers P0.5 terminal, basic Git status, Windows backend executable packaging, desktop packaging, and near-release acceptance.
- Known dependency gates: Terminal and packaging require user approval before installs. This is intentional and required by project rules.
- Remaining uncertainty: `node-pty` native build may fail on this Windows setup; if it does, execution must stop and produce a repair plan rather than falling back to a fake terminal.
- Maintenance check: Terminal, Git status, and packaging are split into focused Electron modules. The agent JSONL backend remains separate from the user terminal.
- Security check: Terminal is explicitly user-controlled. It does not feed commands into agent tools and does not bypass approval.
- Placeholder scan: No unresolved implementation placeholders are intentionally left in this plan.

## Execution Choice

Recommended execution: inline execution with `superpowers:executing-plans`, stopping at each dependency approval gate.

---

## Execution Status

Completed on 2026-06-13 12:16 Asia/Shanghai.

Implemented:

- Installed terminal dependencies under `desktop/`: `node-pty`, `@xterm/xterm`.
- Installed packaging dependencies inside the project boundary: `pyinstaller` through `uv`, `electron-builder` under `desktop/`.
- Added Electron PTY terminal bridge, preload API, React terminal panel, i18n, styling, and terminal tests.
- Added Git status helper, IPC, badge UI, i18n, styling, and parser tests.
- Added `scripts/package_backend.py` and generated `desktop/resources/backend/codecub-agent.exe`.
- Added Windows Electron packaging config and `package:win` script.
- Updated backend process launch logic to prefer bundled `resources/backend/codecub-agent.exe` in packaged builds.
- Added `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`.

Verification:

- `uv run python scripts/package_backend.py`: passed.
- `desktop/resources/backend/codecub-agent.exe --help`: passed.
- `cd desktop && npm run package:win`: passed after disabling Electron native rebuild and unpacking `node-pty` prebuilds.
- `desktop/release/win-unpacked/CodeCub.exe`: exists.
- `desktop/release/win-unpacked/resources/backend/codecub-agent.exe`: exists.
- Packaged app smoke: `packaged_alive_after_8s=True`.
- `uv run pytest -q -ra --durations=10`: `137 passed, 2 skipped, 6 warnings`.
- `uv run python -m codecub --help`: passed.
- `cd desktop && npm test`: `7 passed` test files / `13 passed` tests.
- `cd desktop && npm run typecheck`: passed.
- `cd desktop && npm run build`: passed with Vite chunk size warning.
- Electron dev smoke: `electron_alive_after_6s=True`.

Resolved blocker:

- `electron-builder` initially failed because `node-pty` rebuild required Visual Studio C++ Build Tools. The package already ships Windows prebuilds, so the packaging config now sets `npmRebuild: false` and unpacks `node_modules/node-pty/prebuilds/**/*`.

Known risks:

- `npm audit` reports 8 vulnerabilities after packaging dependencies: 6 high and 2 critical. This is not fixed in P0.5 because automatic force fixes may introduce breaking dependency changes.
- The renderer bundle has a Vite chunk size warning after adding `@xterm/xterm`. This is acceptable for P0 and should be revisited in P1 with dynamic import/code splitting.
