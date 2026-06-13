# CodeCub P0.3 Desktop Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable CodeCub Electron desktop shell that can select a project, launch the local `codecub --app-mode` backend, send user messages, render assistant responses, show backend events in a run log sidebar, stop a run, and persist basic app settings/recent projects.

**Architecture:** P0.3 adds a new `desktop/` app without changing backend behavior. Electron main owns project selection, backend subprocess lifecycle, appData persistence, and IPC; preload exposes a typed API; React renderer owns Chinese-first UI, i18n, chat state, run log state, and settings views.

**Tech Stack:** Electron, Vite, React, TypeScript, Vitest, Node.js child_process, JSONL over stdin/stdout, existing Python backend command `python -m codecub --app-mode`.

---

## 1. Requirement Summary

P0.3 implements the desktop shell stage from `.codecub/spec/2026-06-11-codecub-p0-requirements.md` and `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`.

Implemented in this stage:

- Create `desktop/`.
- Electron + Vite + React + TypeScript.
- Chinese-first UI with English i18n file present.
- Welcome / recent projects screen.
- Project folder selection.
- Backend subprocess launch after project selection.
- JSONL backend event reader.
- Commands to backend stdin: `send_message`, `cancel_run`, `close`.
- Main chat view.
- Run log sidebar.
- Basic settings view.
- Recent project persistence under Electron appData.
- Stop button wired to `cancel_run`.
- Dev-mode backend command: `python -m codecub --app-mode --cwd <project>`.

Intentionally not fully implemented in this stage:

- Real approval blocking and continuation. P0.1 backend returns `unsupported_until_p0_4`.
- Diff preview content from backend tool events.
- Full interactive terminal.
- `.pico/` import workflow.
- Windows packaging and bundled `codecub-agent.exe`.
- Multi-project simultaneous sessions.

P0.3 may show disabled or placeholder UI entry points for approval, diff, terminal, and legacy import, but those controls must be visually secondary and must not claim the feature is working.

## 2. Current Repository Facts

- Backend package is now `codecub/`.
- Backend app-mode entry is `python -m codecub --app-mode --cwd <project>`.
- Backend stdout emits JSONL events with fields: `type`, `timestamp`, `session_id`, `run_id`, `payload`.
- Backend stdin accepts JSON commands with types: `send_message`, `approve_operation`, `reject_operation`, `cancel_run`, `close`.
- `desktop/` does not exist.
- Node/Electron dependencies are not installed.
- Creating `desktop/` and running `npm install` requires explicit user confirmation before execution.

## 3. Unresolved Points And Assumptions

No unresolved requirement should block writing the plan.

Low-risk assumptions:

- P0.3 development uses local source backend through `uv run python -m codecub --app-mode` when available, with `python -m codecub --app-mode` as the fallback command.
- P0.3 stores API key source as text such as `environment`; it must not store actual API key values.
- P0.3 uses a single active project/session at a time.
- P0.3 UI defaults to Chinese through `zh-CN`, while `en-US` exists for the i18n architecture.

Execution blocker:

- Do not create `desktop/`, run `npm`, or download/install packages until the user explicitly confirms dependency setup.

## 4. Planned File Structure

Create:

```text
desktop/
  package.json
  tsconfig.json
  tsconfig.node.json
  vite.config.ts
  index.html
  electron/
    main.ts
    preload.ts
    backendProcess.ts
    appConfig.ts
    projectStore.ts
    ipcTypes.ts
  src/
    main.tsx
    App.tsx
    i18n/
      zh-CN.ts
      en-US.ts
      index.ts
    state/
      backendEvents.ts
      sessionIndex.ts
      chatState.ts
    components/
      WelcomePage.tsx
      ProjectSessionPage.tsx
      ChatView.tsx
      RunLogSidebar.tsx
      SettingsPage.tsx
      Toolbar.tsx
    styles/
      app.css
  tests/
    backendEvents.test.ts
    sessionIndex.test.ts
    chatState.test.ts
```

Responsibilities:

- `electron/main.ts`: create window, register IPC, own app lifecycle.
- `electron/preload.ts`: expose typed `window.codecub`.
- `electron/backendProcess.ts`: spawn backend, write JSONL commands, parse stdout line buffer, stop/close process.
- `electron/appConfig.ts`: load/save app settings in appData JSON.
- `electron/projectStore.ts`: load/save recent projects and session index in appData JSON.
- `electron/ipcTypes.ts`: shared IPC type names and payload types.
- `src/state/backendEvents.ts`: validate and normalize backend events.
- `src/state/sessionIndex.ts`: reducer/helpers for recent projects and active session metadata.
- `src/state/chatState.ts`: reducer/helpers for chat messages and event-to-chat projection.
- `src/i18n/*`: string tables and translator helper.
- `src/components/*`: desktop UI.

## 5. Backup Requirement

Before modifying existing files for P0.3, create a backup folder:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-p0-3-desktop-shell"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".codecub\plan\2026-06-11-codecub-p0-3-desktop-shell-plan.md" -Destination $backup -Force
```

If P0.3 execution modifies existing repository files such as `.gitignore`, `README.md`, or root configuration, back up each file before editing. Creating new files under `desktop/` does not require backup.

## 6. Implementation Tasks

### Task 1: Confirm Dependency Setup And Create Project Skeleton

**Files:**

- Create: `desktop/package.json`
- Create: `desktop/tsconfig.json`
- Create: `desktop/tsconfig.node.json`
- Create: `desktop/vite.config.ts`
- Create: `desktop/index.html`

- [ ] **Step 1: Ask for explicit dependency setup approval**

Before any filesystem creation or dependency install, report:

```text
准备创建目录：D:\代码备份\pico\pico-main\desktop
准备安装依赖：
- runtime: react, react-dom
- dev: electron, vite, typescript, @vitejs/plugin-react, vitest, jsdom, @testing-library/react, @testing-library/jest-dom, @types/node, @types/react, @types/react-dom, concurrently, wait-on
准备生成：desktop/package.json 和 package-lock.json
是否确认？
```

Expected:

- Continue only after user says confirm.

- [ ] **Step 2: Create `desktop/package.json`**

Create:

```json
{
  "name": "codecub-desktop",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "dist-electron/main.js",
  "scripts": {
    "dev": "concurrently -k \"vite --host 127.0.0.1\" \"tsc -p tsconfig.node.json --watch\" \"wait-on http://127.0.0.1:5173 && electron dist-electron/main.js\"",
    "build": "tsc -p tsconfig.json && tsc -p tsconfig.node.json && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc -p tsconfig.json && tsc -p tsconfig.node.json"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.0.0",
    "@testing-library/react": "^16.0.0",
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^5.0.0",
    "concurrently": "^9.0.0",
    "electron": "^38.0.0",
    "jsdom": "^27.0.0",
    "typescript": "^5.0.0",
    "vite": "^7.0.0",
    "vitest": "^3.0.0",
    "wait-on": "^8.0.0"
  }
}
```

- [ ] **Step 3: Create TypeScript and Vite config**

Create `desktop/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "tests", "electron/preload.ts"]
}
```

Create `desktop/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "outDir": "dist-electron",
    "rootDir": "electron",
    "types": ["node", "electron"]
  },
  "include": ["electron/**/*.ts"]
}
```

Create `desktop/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
  build: {
    outDir: "dist-renderer",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [],
  },
});
```

Create `desktop/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CodeCub</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Install dependencies after approval**

Run:

```powershell
cd desktop
npm install
```

Expected:

- `desktop/node_modules/` exists.
- `desktop/package-lock.json` exists.

### Task 2: Backend Event Parser And Chat Projection

**Files:**

- Create: `desktop/src/state/backendEvents.ts`
- Create: `desktop/src/state/chatState.ts`
- Create: `desktop/tests/backendEvents.test.ts`
- Create: `desktop/tests/chatState.test.ts`

- [ ] **Step 1: Write backend event tests**

Create `desktop/tests/backendEvents.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseBackendEventLine } from "../src/state/backendEvents";

describe("parseBackendEventLine", () => {
  it("parses a valid run_completed event", () => {
    const event = parseBackendEventLine(
      '{"type":"run_completed","timestamp":"2026-06-11T00:00:00Z","session_id":"s1","run_id":"r1","payload":{"final":"done"}}',
    );

    expect(event.type).toBe("run_completed");
    expect(event.timestamp).toBe("2026-06-11T00:00:00Z");
    expect(event.session_id).toBe("s1");
    expect(event.run_id).toBe("r1");
    expect(event.payload.final).toBe("done");
  });

  it("rejects invalid JSON", () => {
    expect(() => parseBackendEventLine("{bad json")).toThrow("Invalid backend event JSON");
  });

  it("rejects missing required fields", () => {
    expect(() => parseBackendEventLine('{"type":"run_completed"}')).toThrow("Backend event missing required field");
  });
});
```

- [ ] **Step 2: Write chat projection tests**

Create `desktop/tests/chatState.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyBackendEvent, createInitialChatState } from "../src/state/chatState";

describe("applyBackendEvent", () => {
  it("records user and assistant messages from backend events", () => {
    let state = createInitialChatState();

    state = applyBackendEvent(state, {
      type: "user_message_received",
      timestamp: "2026-06-11T00:00:00Z",
      session_id: "s1",
      run_id: "r1",
      payload: { message: "你好" },
    });

    state = applyBackendEvent(state, {
      type: "assistant_message",
      timestamp: "2026-06-11T00:00:01Z",
      session_id: "s1",
      run_id: "r1",
      payload: { text: "你好，我是 CodeCub。" },
    });

    expect(state.messages).toHaveLength(2);
    expect(state.messages[0].role).toBe("user");
    expect(state.messages[1].role).toBe("assistant");
    expect(state.messages[1].content).toContain("CodeCub");
  });
});
```

- [ ] **Step 3: Implement event parser**

Create `desktop/src/state/backendEvents.ts`:

```ts
export type BackendEvent = {
  type: string;
  timestamp: string;
  session_id: string;
  run_id: string;
  payload: Record<string, unknown>;
};

const requiredFields = ["type", "timestamp", "session_id", "run_id"] as const;

export function parseBackendEventLine(line: string): BackendEvent {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch (error) {
    throw new Error("Invalid backend event JSON", { cause: error });
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Backend event must be an object");
  }

  const event = parsed as Partial<BackendEvent>;
  for (const field of requiredFields) {
    if (typeof event[field] !== "string") {
      throw new Error(`Backend event missing required field: ${field}`);
    }
  }

  return {
    type: event.type,
    timestamp: event.timestamp,
    session_id: event.session_id,
    run_id: event.run_id,
    payload: isRecord(event.payload) ? event.payload : {},
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
```

- [ ] **Step 4: Implement chat state**

Create `desktop/src/state/chatState.ts`:

```ts
import type { BackendEvent } from "./backendEvents";

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  runId: string;
  createdAt: string;
};

export type ChatState = {
  messages: ChatMessage[];
  activeRunId: string;
  isRunning: boolean;
};

export function createInitialChatState(): ChatState {
  return {
    messages: [],
    activeRunId: "",
    isRunning: false,
  };
}

export function applyBackendEvent(state: ChatState, event: BackendEvent): ChatState {
  if (event.type === "user_message_received") {
    return {
      ...state,
      activeRunId: event.run_id,
      isRunning: true,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:user:${state.messages.length}`,
          role: "user",
          content: String(event.payload.message ?? ""),
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }

  if (event.type === "assistant_message") {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:assistant:${state.messages.length}`,
          role: "assistant",
          content: String(event.payload.text ?? event.payload.final ?? ""),
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }

  if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_canceled") {
    return {
      ...state,
      activeRunId: "",
      isRunning: false,
    };
  }

  return state;
}
```

- [ ] **Step 5: Run state tests**

Run:

```powershell
cd desktop
npm test -- backendEvents.test.ts chatState.test.ts
```

Expected:

- Tests pass.

### Task 3: Session Index And Recent Project Persistence Model

**Files:**

- Create: `desktop/src/state/sessionIndex.ts`
- Create: `desktop/tests/sessionIndex.test.ts`

- [ ] **Step 1: Write session index tests**

Create `desktop/tests/sessionIndex.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { upsertRecentProject } from "../src/state/sessionIndex";

describe("upsertRecentProject", () => {
  it("adds a new project and keeps newest first", () => {
    const items = upsertRecentProject([], {
      path: "D:/repo",
      name: "repo",
      lastSessionId: "s1",
      lastUsedAt: "2026-06-11T00:00:00Z",
    });

    expect(items).toHaveLength(1);
    expect(items[0].path).toBe("D:/repo");
  });

  it("updates an existing project instead of duplicating it", () => {
    const items = upsertRecentProject(
      [
        {
          path: "D:/repo",
          name: "repo",
          lastSessionId: "old",
          lastUsedAt: "2026-06-10T00:00:00Z",
        },
      ],
      {
        path: "D:/repo",
        name: "repo",
        lastSessionId: "new",
        lastUsedAt: "2026-06-11T00:00:00Z",
      },
    );

    expect(items).toHaveLength(1);
    expect(items[0].lastSessionId).toBe("new");
  });
});
```

- [ ] **Step 2: Implement session index helpers**

Create `desktop/src/state/sessionIndex.ts`:

```ts
export type RecentProject = {
  path: string;
  name: string;
  lastSessionId: string;
  lastUsedAt: string;
};

export type SessionIndexItem = {
  projectPath: string;
  sessionId: string;
  title: string;
  createdAt: string;
  lastUsedAt: string;
  provider: string;
  model: string;
  lastMessage: string;
};

export function upsertRecentProject(items: RecentProject[], project: RecentProject): RecentProject[] {
  return [project, ...items.filter((item) => item.path !== project.path)].slice(0, 20);
}
```

- [ ] **Step 3: Run session tests**

Run:

```powershell
cd desktop
npm test -- sessionIndex.test.ts
```

Expected:

- Tests pass.

### Task 4: Electron Main, Preload, App Config, And Backend Process

**Files:**

- Create: `desktop/electron/ipcTypes.ts`
- Create: `desktop/electron/appConfig.ts`
- Create: `desktop/electron/projectStore.ts`
- Create: `desktop/electron/backendProcess.ts`
- Create: `desktop/electron/preload.ts`
- Create: `desktop/electron/main.ts`

- [ ] **Step 1: Create IPC types**

Create `desktop/electron/ipcTypes.ts`:

```ts
export type AppSettings = {
  language: "zh-CN" | "en-US";
  provider: "openai" | "ollama" | "anthropic";
  model: string;
  baseUrl: string;
  approvalPolicy: "ask" | "auto" | "never";
  apiKeySource: "environment";
};

export type OpenProjectResult = {
  canceled: boolean;
  projectPath: string;
};

export type BackendCommand =
  | { type: "send_message"; message: string; run_id?: string }
  | { type: "cancel_run"; run_id?: string }
  | { type: "close" };
```

- [ ] **Step 2: Create app config persistence**

Create `desktop/electron/appConfig.ts`:

```ts
import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { AppSettings } from "./ipcTypes";

export const defaultSettings: AppSettings = {
  language: "zh-CN",
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://www.right.codes/codex/v1",
  approvalPolicy: "ask",
  apiKeySource: "environment",
};

function settingsPath(): string {
  return join(app.getPath("userData"), "settings.json");
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    const raw = await readFile(settingsPath(), "utf-8");
    return { ...defaultSettings, ...JSON.parse(raw) } as AppSettings;
  } catch {
    return defaultSettings;
  }
}

export async function saveSettings(settings: AppSettings): Promise<AppSettings> {
  await mkdir(app.getPath("userData"), { recursive: true });
  const safeSettings: AppSettings = { ...settings, apiKeySource: "environment" };
  await writeFile(settingsPath(), JSON.stringify(safeSettings, null, 2), "utf-8");
  return safeSettings;
}
```

- [ ] **Step 3: Create recent project persistence**

Create `desktop/electron/projectStore.ts`:

```ts
import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import type { RecentProject } from "../src/state/sessionIndex";
import { upsertRecentProject } from "../src/state/sessionIndex";

function recentProjectsPath(): string {
  return join(app.getPath("userData"), "recent-projects.json");
}

export async function loadRecentProjects(): Promise<RecentProject[]> {
  try {
    const raw = await readFile(recentProjectsPath(), "utf-8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as RecentProject[]) : [];
  } catch {
    return [];
  }
}

export async function rememberProject(projectPath: string, sessionId = ""): Promise<RecentProject[]> {
  const current = await loadRecentProjects();
  const updated = upsertRecentProject(current, {
    path: projectPath,
    name: basename(projectPath),
    lastSessionId: sessionId,
    lastUsedAt: new Date().toISOString(),
  });
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(recentProjectsPath(), JSON.stringify(updated, null, 2), "utf-8");
  return updated;
}
```

- [ ] **Step 4: Create backend process manager**

Create `desktop/electron/backendProcess.ts`:

```ts
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import type { BackendCommand } from "./ipcTypes";

export type BackendProcessEvents = {
  event: [line: string];
  exit: [code: number | null];
  error: [message: string];
};

export class BackendProcess extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private stdoutBuffer = "";

  start(projectPath: string): void {
    this.stop();
    const command = process.env.CODECUB_BACKEND_COMMAND || "uv";
    const args = process.env.CODECUB_BACKEND_COMMAND
      ? ["-m", "codecub", "--app-mode", "--cwd", projectPath]
      : ["run", "python", "-m", "codecub", "--app-mode", "--cwd", projectPath];

    this.child = spawn(command, args, {
      cwd: projectPath,
      env: process.env,
      shell: false,
    });

    this.child.stdout.on("data", (chunk: Buffer) => {
      this.handleStdout(chunk.toString("utf-8"));
    });

    this.child.stderr.on("data", (chunk: Buffer) => {
      this.emit("error", chunk.toString("utf-8"));
    });

    this.child.on("exit", (code) => {
      this.emit("exit", code);
      this.child = null;
    });
  }

  send(command: BackendCommand): void {
    if (!this.child) {
      this.emit("error", "Backend is not running");
      return;
    }
    const payload = command.type === "send_message" && !command.run_id
      ? { ...command, run_id: `run_${randomUUID()}` }
      : command;
    this.child.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  stop(): void {
    if (!this.child) {
      return;
    }
    this.child.stdin.write(`${JSON.stringify({ type: "close" })}\n`);
    this.child.kill();
    this.child = null;
  }

  private handleStdout(text: string): void {
    this.stdoutBuffer += text;
    let newlineIndex = this.stdoutBuffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.stdoutBuffer.slice(0, newlineIndex).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      if (line) {
        this.emit("event", line);
      }
      newlineIndex = this.stdoutBuffer.indexOf("\n");
    }
  }
}
```

- [ ] **Step 5: Create preload API**

Create `desktop/electron/preload.ts`:

```ts
import { contextBridge, ipcRenderer } from "electron";
import type { AppSettings, BackendCommand, OpenProjectResult } from "./ipcTypes";

const api = {
  openProject: (): Promise<OpenProjectResult> => ipcRenderer.invoke("project:open"),
  startBackend: (projectPath: string): Promise<void> => ipcRenderer.invoke("backend:start", projectPath),
  sendBackendCommand: (command: BackendCommand): Promise<void> => ipcRenderer.invoke("backend:send", command),
  stopBackend: (): Promise<void> => ipcRenderer.invoke("backend:stop"),
  loadSettings: (): Promise<AppSettings> => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings: AppSettings): Promise<AppSettings> => ipcRenderer.invoke("settings:save", settings),
  loadRecentProjects: () => ipcRenderer.invoke("projects:recent"),
  onBackendEvent: (callback: (line: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, line: string) => callback(line);
    ipcRenderer.on("backend:event", listener);
    return () => ipcRenderer.off("backend:event", listener);
  },
  onBackendError: (callback: (message: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, message: string) => callback(message);
    ipcRenderer.on("backend:error", listener);
    return () => ipcRenderer.off("backend:error", listener);
  },
};

contextBridge.exposeInMainWorld("codecub", api);

declare global {
  interface Window {
    codecub: typeof api;
  }
}
```

- [ ] **Step 6: Create Electron main**

Create `desktop/electron/main.ts`:

```ts
import { BrowserWindow, app, dialog, ipcMain } from "electron";
import { join } from "node:path";
import { BackendProcess } from "./backendProcess";
import { loadSettings, saveSettings } from "./appConfig";
import { loadRecentProjects, rememberProject } from "./projectStore";
import type { BackendCommand } from "./ipcTypes";

let mainWindow: BrowserWindow | null = null;
const backend = new BackendProcess();

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: "CodeCub",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(join(__dirname, "../dist-renderer/index.html"));
  }
}

app.whenReady().then(() => {
  createWindow();

  backend.on("event", (line) => mainWindow?.webContents.send("backend:event", line));
  backend.on("error", (message) => mainWindow?.webContents.send("backend:error", message));
  backend.on("exit", (code) => mainWindow?.webContents.send("backend:error", `Backend exited: ${code ?? "unknown"}`));
});

app.on("window-all-closed", () => {
  backend.stop();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

ipcMain.handle("project:open", async () => {
  const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) {
    return { canceled: true, projectPath: "" };
  }
  await rememberProject(result.filePaths[0]);
  return { canceled: false, projectPath: result.filePaths[0] };
});

ipcMain.handle("backend:start", async (_event, projectPath: string) => {
  backend.start(projectPath);
  await rememberProject(projectPath);
});

ipcMain.handle("backend:send", async (_event, command: BackendCommand) => {
  backend.send(command);
});

ipcMain.handle("backend:stop", async () => {
  backend.send({ type: "cancel_run" });
});

ipcMain.handle("settings:load", async () => loadSettings());
ipcMain.handle("settings:save", async (_event, settings) => saveSettings(settings));
ipcMain.handle("projects:recent", async () => loadRecentProjects());
```

- [ ] **Step 7: Typecheck Electron**

Run:

```powershell
cd desktop
npm run typecheck
```

Expected:

- TypeScript compiles.

### Task 5: i18n And React UI Skeleton

**Files:**

- Create: `desktop/src/i18n/zh-CN.ts`
- Create: `desktop/src/i18n/en-US.ts`
- Create: `desktop/src/i18n/index.ts`
- Create: `desktop/src/main.tsx`
- Create: `desktop/src/App.tsx`
- Create: `desktop/src/components/WelcomePage.tsx`
- Create: `desktop/src/components/ProjectSessionPage.tsx`
- Create: `desktop/src/components/ChatView.tsx`
- Create: `desktop/src/components/RunLogSidebar.tsx`
- Create: `desktop/src/components/SettingsPage.tsx`
- Create: `desktop/src/components/Toolbar.tsx`
- Create: `desktop/src/styles/app.css`

- [ ] **Step 1: Create i18n files**

Create `desktop/src/i18n/zh-CN.ts`:

```ts
export const zhCN = {
  appName: "CodeCub",
  openProject: "打开项目",
  recentProjects: "最近项目",
  noRecentProjects: "还没有最近项目",
  settings: "设置",
  back: "返回",
  provider: "模型服务",
  model: "模型",
  baseUrl: "Base URL",
  approvalPolicy: "审批模式",
  apiKeySource: "API Key 来源",
  language: "界面语言",
  send: "发送",
  stop: "停止",
  inputPlaceholder: "输入任务，CodeCub 会在当前项目中执行",
  runLog: "运行日志",
  backendNotStarted: "后端尚未启动",
  terminalUnavailable: "终端面板将在 P0.5 启用",
  diffUnavailable: "Diff 预览将在 P0.4 启用",
  approvalUnavailable: "审批弹窗将在 P0.4 启用",
};
```

Create `desktop/src/i18n/en-US.ts`:

```ts
export const enUS = {
  appName: "CodeCub",
  openProject: "Open Project",
  recentProjects: "Recent Projects",
  noRecentProjects: "No recent projects yet",
  settings: "Settings",
  back: "Back",
  provider: "Provider",
  model: "Model",
  baseUrl: "Base URL",
  approvalPolicy: "Approval Policy",
  apiKeySource: "API Key Source",
  language: "Language",
  send: "Send",
  stop: "Stop",
  inputPlaceholder: "Describe a task for CodeCub to run in this project",
  runLog: "Run Log",
  backendNotStarted: "Backend has not started",
  terminalUnavailable: "Terminal panel will be enabled in P0.5",
  diffUnavailable: "Diff preview will be enabled in P0.4",
  approvalUnavailable: "Approval dialogs will be enabled in P0.4",
};
```

Create `desktop/src/i18n/index.ts`:

```ts
import { enUS } from "./en-US";
import { zhCN } from "./zh-CN";

export type Locale = "zh-CN" | "en-US";
export type I18nKey = keyof typeof zhCN;

const dictionaries = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

export function t(locale: Locale, key: I18nKey): string {
  return dictionaries[locale][key] ?? zhCN[key];
}
```

- [ ] **Step 2: Create renderer entry**

Create `desktop/src/main.tsx`:

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/app.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 3: Create App component**

Create `desktop/src/App.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { parseBackendEventLine, type BackendEvent } from "./state/backendEvents";
import { applyBackendEvent, createInitialChatState } from "./state/chatState";
import type { RecentProject } from "./state/sessionIndex";
import { t, type Locale } from "./i18n";
import { WelcomePage } from "./components/WelcomePage";
import { ProjectSessionPage } from "./components/ProjectSessionPage";
import { SettingsPage } from "./components/SettingsPage";

type View = "welcome" | "session" | "settings";

export function App() {
  const [locale, setLocale] = useState<Locale>("zh-CN");
  const [view, setView] = useState<View>("welcome");
  const [projectPath, setProjectPath] = useState("");
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>([]);
  const [events, setEvents] = useState<BackendEvent[]>([]);
  const [chatState, setChatState] = useState(createInitialChatState());
  const translate = useMemo(() => (key: Parameters<typeof t>[1]) => t(locale, key), [locale]);

  useEffect(() => {
    window.codecub.loadRecentProjects().then(setRecentProjects);
    window.codecub.loadSettings().then((settings) => setLocale(settings.language));
    return window.codecub.onBackendEvent((line) => {
      const event = parseBackendEventLine(line);
      setEvents((current) => [...current, event]);
      setChatState((current) => applyBackendEvent(current, event));
    });
  }, []);

  async function openProject() {
    const result = await window.codecub.openProject();
    if (result.canceled) {
      return;
    }
    setProjectPath(result.projectPath);
    await window.codecub.startBackend(result.projectPath);
    setRecentProjects(await window.codecub.loadRecentProjects());
    setView("session");
  }

  async function sendMessage(message: string) {
    await window.codecub.sendBackendCommand({ type: "send_message", message });
  }

  async function stopRun() {
    await window.codecub.sendBackendCommand({ type: "cancel_run", run_id: chatState.activeRunId });
  }

  if (view === "settings") {
    return <SettingsPage locale={locale} setLocale={setLocale} t={translate} onBack={() => setView(projectPath ? "session" : "welcome")} />;
  }

  if (view === "session") {
    return (
      <ProjectSessionPage
        t={translate}
        projectPath={projectPath}
        events={events}
        chatState={chatState}
        onSend={sendMessage}
        onStop={stopRun}
        onSettings={() => setView("settings")}
      />
    );
  }

  return <WelcomePage t={translate} recentProjects={recentProjects} onOpenProject={openProject} onSettings={() => setView("settings")} />;
}
```

- [ ] **Step 4: Create UI components**

Create each component with props matching `App.tsx`.

`WelcomePage` must render:

- CodeCub name as first-viewport signal.
- Open project button.
- Recent project list.
- Small restrained pet branding text as UI label only, not a marketing hero.

`ProjectSessionPage` must render:

- top toolbar with project path and Settings button.
- chat area.
- run log sidebar.
- disabled status strip for P0.4/P0.5 features.

`ChatView` must render:

- stable scrollable message list.
- fixed-height input bar.
- Send and Stop buttons.

`RunLogSidebar` must render:

- event type.
- timestamp.
- run id.
- compact payload summary.

`SettingsPage` must render:

- provider select.
- model input.
- base URL input.
- approval policy select.
- language select.
- API key source display fixed to environment.

- [ ] **Step 5: Create CSS**

Create `desktop/src/styles/app.css` with:

```css
:root {
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #1d2329;
  background: #f6f7f9;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 960px;
  min-height: 640px;
}

button,
input,
select,
textarea {
  font: inherit;
}

.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.toolbar {
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid #d9dee5;
  background: #ffffff;
}

.workspace-layout {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  min-height: 0;
}

.chat-view {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 0;
}

.message-list {
  overflow: auto;
  padding: 16px;
}

.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid #d9dee5;
  background: #ffffff;
}

.run-log {
  border-left: 1px solid #d9dee5;
  background: #ffffff;
  overflow: auto;
}
```

- [ ] **Step 6: Build renderer**

Run:

```powershell
cd desktop
npm run build
```

Expected:

- Build passes.

### Task 6: Manual Dev Smoke Test

**Files:**

- Read: `desktop/`
- Read: `codecub/`

- [ ] **Step 1: Run backend app-mode smoke test**

Run from repository root:

```powershell
'{"type":"send_message","message":"请用一句话介绍 CodeCub"}' | uv run python -m codecub --app-mode --cwd .
```

Expected:

- stdout contains JSONL events.
- includes `session_started`.
- includes `assistant_message` or `run_failed`.

- [ ] **Step 2: Run desktop dev server**

Run:

```powershell
cd desktop
npm run dev
```

Expected:

- Electron window opens.
- Welcome page appears in Chinese.
- Open project action opens folder picker.
- After selecting project, backend starts.
- Sending a message creates user message, backend log events, assistant message or backend error.

- [ ] **Step 3: Browser or screenshot verification**

Use the Browser plugin or an available local UI inspection tool only if the dev server exposes a local URL. For Electron-only visual verification, report manual smoke-test observations from the launched app process output and screenshots if available.

Expected:

- UI is nonblank.
- Main session layout has chat and run log sidebar.
- Text does not overlap at 1280x820.

## 7. Verification Commands

After dependency setup:

```powershell
cd desktop
npm test
npm run typecheck
npm run build
```

Backend compatibility:

```powershell
uv run python -m codecub --help
'{"type":"send_message","message":"hello"}' | uv run python -m codecub --app-mode --cwd .
```

Selected backend regression after P0.3:

```powershell
uv run pytest tests/test_app_protocol.py tests/test_app_runner.py tests/test_pico.py tests/test_safety_invariants.py tests/test_context_manager.py tests/test_memory.py tests/test_run_store.py tests/test_task_state.py -q
```

## 8. Stop Conditions

Stop and report if:

- User has not approved `desktop/` creation and npm dependency installation.
- `desktop/` already exists with unrelated content.
- npm install fails due to network, registry, or version resolution.
- Electron cannot launch on this Windows environment.
- Backend subprocess cannot start `codecub --app-mode`.
- UI requires backend protocol events not emitted by P0.1/P0.2.
- Any change is needed outside `desktop/`, `.codecub/plan`, `.gitignore`, or documentation.
- Tests reveal a backend regression unrelated to the desktop shell.

## 9. Plan Review

### Requirement Coverage

- Create desktop app under `desktop/`: Task 1.
- Project folder selection: Task 4 and Task 5.
- Backend subprocess lifecycle: Task 4.
- JSONL event reader: Task 2 and Task 4.
- Send user messages and stop signals: Task 4 and Task 5.
- AppData settings and recent projects: Task 3 and Task 4.
- Chat UI: Task 5.
- Run log sidebar: Task 5.
- Settings UI: Task 5.
- Chinese default with i18n architecture: Task 5.
- P0.3 boundary for approval, diff, terminal, `.pico` import, packaging: Sections 1 and 8.

### Placeholder Scan

This plan avoids unresolved implementation placeholders. The only future-stage features named here are explicitly out of P0.3 scope and are shown as disabled UI states.

### Type Consistency

- Backend event type uses `type`, `timestamp`, `session_id`, `run_id`, and `payload`, matching `codecub/app_protocol.py`.
- Backend commands use `send_message`, `cancel_run`, and `close`, matching `codecub/app_protocol.py`.
- `ChatState`, `RecentProject`, and `AppSettings` are defined before component usage.

### Maintenance Review

The plan keeps Electron main, preload, renderer state, and components separated. It avoids storing secrets and keeps backend protocol adaptation in `backendProcess.ts`, so P0.4 can add approvals and diffs without rewriting the UI shell.

## 10. P0.3 Execution Status

- Started: 2026-06-12 17:18 Asia/Shanghai.
- Completed: desktop shell implementation and available verification completed on 2026-06-12 17:34 Asia/Shanghai.
- Plan status backup path:
  - `E:\codex_backup\20260612-173437-codecub-p0-3-plan-execution-status`
- Unicode Git root fix backup path:
  - `E:\codex_backup\20260612-173220-codecub-p0-3-unicode-git-root-fix`
- Files created:
  - `desktop/package.json`
  - `desktop/package-lock.json`
  - `desktop/tsconfig.json`
  - `desktop/tsconfig.node.json`
  - `desktop/vite.config.ts`
  - `desktop/index.html`
  - `desktop/electron/`
  - `desktop/src/`
  - `desktop/tests/`
  - `tests/test_workspace.py`
- Files modified:
  - `codecub/workspace.py`
- Implementation completed:
  - Electron main/preload skeleton.
  - Backend subprocess manager for `uv run python -m codecub --app-mode --cwd <project>`.
  - JSONL backend event parsing.
  - Chat state projection.
  - Recent project/session index helpers.
  - Chinese-first React UI with English i18n file.
  - Welcome page, project session page, chat view, run log sidebar, settings page.
  - `.codecub` backend path now works under current Windows Chinese workspace path after explicit UTF-8 Git stdout decoding.
- Tests and verification:
  - `cd desktop; npm test`: 3 test files passed, 6 tests passed.
  - `cd desktop; npm run typecheck`: passed.
  - `cd desktop; npm run build`: passed.
  - `uv run pytest tests/test_workspace.py -q`: 1 passed.
  - app-mode smoke test: `{"type":"send_message","message":"请用一句话介绍 CodeCub"} | uv run python -m codecub --app-mode --cwd .` returned JSONL events and completed.
  - Backend selected regression with workspace test: 113 passed, 2 skipped.
- Known verification gap:
  - Browser/in-app browser screenshot verification was not completed because no Browser control tool was exposed in this turn. Build, typecheck, unit tests, backend smoke, and backend regression were completed instead.
- Known npm risk:
  - `npm install` completed but reported 3 audit vulnerabilities. No automatic `npm audit fix --force` was run because it may introduce dependency changes outside the approved implementation plan.
