# CodeCub Model API Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to configure model provider, model name, base URL/host, and API key directly in the CodeCub desktop settings UI while storing API keys only in OS-backed secure storage.

**Architecture:** Desktop settings will persist only non-secret provider metadata in appData JSON. API keys entered in the renderer will be sent once through IPC to Electron main, stored with `keytar`, and never returned to the renderer as plaintext. Electron main will retrieve the credential at backend launch time, pass provider settings through CLI arguments, and inject credentials into the backend child process environment.

**Tech Stack:** Electron main/preload IPC, React + TypeScript renderer, `keytar` for OS credential storage, existing Python `codecub` CLI provider flags, Vitest for focused TypeScript tests, existing pytest suite for backend regression.

---

## Requirement Summary

The approved P0 requirement update is in `.codecub/spec/2026-06-11-codecub-p0-requirements.md`.

Required behavior:

- Settings page exposes provider type, model, base URL or Ollama host, API key entry/update/clear, approval policy, and UI language.
- API keys entered in the frontend are stored through OS-backed secure storage.
- `settings.json`, project `.codecub/`, logs, traces, reports, and renderer-visible settings responses must not contain full API keys.
- Environment variables remain supported as fallback, but normal desktop usage must not require manual environment variable setup.
- Backend launch must use the saved provider/model/base URL/host and retrieve the API key only at run time.

## Current Code Findings

- `desktop/electron/appConfig.ts` currently persists only `language` and `approvalPolicy`.
- `desktop/electron/backendProcess.ts` starts `codecub --app-mode` with only `--cwd` and `--approval`.
- `desktop/electron/ipcTypes.ts` defines `AppSettings` without provider configuration.
- `desktop/electron/preload.cts` exposes `loadSettings` and `saveSettings`, but no credential-specific operations.
- `desktop/src/components/SettingsPage.tsx` displays API key source as read-only environment text.
- `codecub/cli.py` already supports `--provider`, `--model`, `--base-url`, and `--host`; API keys are read from provider-specific environment variables.

## File Structure

Expected files to modify:

- `desktop/package.json` and `desktop/package-lock.json`: add `keytar` runtime dependency.
- `desktop/electron/ipcTypes.ts`: extend settings and credential request/response types.
- `desktop/electron/appConfig.ts`: load/save non-secret provider configuration.
- `desktop/electron/credentialStore.ts`: new keytar wrapper for API key save/read/delete/status.
- `desktop/electron/backendLaunchConfig.ts`: new pure helper for backend args/env construction.
- `desktop/electron/backendProcess.ts`: accept provider launch config and child process env overrides.
- `desktop/electron/main.ts`: wire settings IPC, credential IPC, and backend launch using saved settings.
- `desktop/electron/preload.cts`: expose typed credential-safe API methods.
- `desktop/src/App.tsx`: load provider settings and pass settings props to `SettingsPage`.
- `desktop/src/components/SettingsPage.tsx`: implement provider/model/base URL/API key UI.
- `desktop/src/i18n/zh-CN.ts` and `desktop/src/i18n/en-US.ts`: add setting labels and messages.
- `desktop/src/styles/app.css`: style compact settings controls and secret status.
- `desktop/tests/*.test.ts` and `desktop/tests/*.test.tsx`: add focused TypeScript regression tests.

Expected backend files:

- No Python provider implementation changes are required for P0, because `codecub/cli.py` already accepts provider/model/base URL/host and reads API keys from env.
- Python tests should still run to confirm no regression.

## Implementation Tasks

### Task 1: Add Secure Storage Dependency

**Files:**

- Modify: `desktop/package.json`
- Modify: `desktop/package-lock.json`

- [ ] **Step 1: Back up dependency manifests**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-keytar-manifests"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\package.json" -Destination (Join-Path $backup "desktop_package.json")
Copy-Item -LiteralPath ".\desktop\package-lock.json" -Destination (Join-Path $backup "desktop_package-lock.json")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Install keytar inside `desktop/`**

Run:

```powershell
npm install keytar --save
```

Working directory:

```text
D:\代码备份\pico\pico-main\desktop
```

Expected:

- `desktop/package.json` includes `"keytar"` under dependencies.
- `desktop/package-lock.json` is updated.
- No files outside the repository are written except normal npm cache locations.

- [ ] **Step 3: Verify dependency metadata**

Run:

```powershell
npm ls keytar
```

Expected: one installed `keytar` entry and exit code 0.

### Task 2: Define Provider Settings Types

**Files:**

- Modify: `desktop/electron/ipcTypes.ts`

- [ ] **Step 1: Back up IPC type file**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-ipc-types"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\electron\ipcTypes.ts" -Destination (Join-Path $backup "desktop_electron_ipcTypes.ts")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Extend settings types**

Add these exported types to `desktop/electron/ipcTypes.ts`:

```ts
export type ModelProvider = "openai" | "anthropic" | "ollama";

export type CredentialStatus = {
  configured: boolean;
  source: "secure-store" | "environment" | "none";
  displayHint: string;
};

export type ProviderSettings = {
  provider: ModelProvider;
  model: string;
  baseUrl: string;
  host: string;
  credential: CredentialStatus;
};

export type SaveProviderSettingsRequest = {
  provider: ModelProvider;
  model: string;
  baseUrl: string;
  host: string;
  apiKey?: string;
  clearApiKey?: boolean;
};
```

Update `AppSettings` to:

```ts
export type AppSettings = {
  language: "zh-CN" | "en-US";
  approvalPolicy: "ask" | "auto" | "never";
  provider: ProviderSettings;
};
```

- [ ] **Step 3: Run typecheck and expect downstream failures**

Run:

```powershell
npm run typecheck
```

Working directory:

```text
D:\代码备份\pico\pico-main\desktop
```

Expected: typecheck may fail because callers still construct `AppSettings` without `provider`. Failures should point to `appConfig.ts`, `SettingsPage.tsx`, or `App.tsx`.

### Task 3: Persist Non-Secret Provider Settings

**Files:**

- Modify: `desktop/electron/appConfig.ts`
- Test: `desktop/tests/appConfig.test.ts`

- [ ] **Step 1: Back up app config file**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-app-config"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\electron\appConfig.ts" -Destination (Join-Path $backup "desktop_electron_appConfig.ts")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Create serializable defaults and sanitizer**

In `desktop/electron/appConfig.ts`, update defaults to include provider settings:

```ts
const defaultProviderSettings: ProviderSettings = {
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://www.right.codes/codex/v1",
  host: "http://127.0.0.1:11434",
  credential: {
    configured: false,
    source: "none",
    displayHint: "not configured",
  },
};

export const defaultSettings: AppSettings = {
  language: "zh-CN",
  approvalPolicy: "ask",
  provider: defaultProviderSettings,
};
```

Add a sanitizer that drops secrets:

```ts
export function sanitizeSettingsForDisk(settings: AppSettings): AppSettings {
  return {
    language: settings.language,
    approvalPolicy: settings.approvalPolicy,
    provider: {
      provider: settings.provider.provider,
      model: settings.provider.model,
      baseUrl: settings.provider.baseUrl,
      host: settings.provider.host,
      credential: {
        configured: settings.provider.credential.configured,
        source: settings.provider.credential.source,
        displayHint: settings.provider.credential.displayHint,
      },
    },
  };
}
```

Update `loadSettings()` to merge old P0 settings safely:

```ts
return {
  ...defaultSettings,
  ...parsed,
  provider: {
    ...defaultProviderSettings,
    ...(parsed.provider ?? {}),
    credential: {
      ...defaultProviderSettings.credential,
      ...(parsed.provider?.credential ?? {}),
    },
  },
} as AppSettings;
```

Update `saveSettings()` so it writes only `sanitizeSettingsForDisk(settings)`.

- [ ] **Step 3: Add app config tests**

Create `desktop/tests/appConfig.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { defaultSettings, sanitizeSettingsForDisk } from "../electron/appConfig";

describe("app settings sanitization", () => {
  it("keeps provider metadata but never serializes a plaintext api key", () => {
    const settings = sanitizeSettingsForDisk({
      ...defaultSettings,
      provider: {
        provider: "openai",
        model: "qwen-flash",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        host: "http://127.0.0.1:11434",
        credential: {
          configured: true,
          source: "secure-store",
          displayHint: "saved",
        },
      },
    });

    const serialized = JSON.stringify(settings);
    expect(serialized).toContain("qwen-flash");
    expect(serialized).toContain("secure-store");
    expect(serialized).not.toContain("sk-test");
    expect(serialized).not.toContain("apiKey");
  });
});
```

- [ ] **Step 4: Run focused test**

Run:

```powershell
npm run test -- appConfig.test.ts
```

Expected: app config test passes.

### Task 4: Add OS Credential Store Wrapper

**Files:**

- Create: `desktop/electron/credentialStore.ts`
- Test: `desktop/tests/credentialStore.test.ts`

- [ ] **Step 1: Create credential store module**

Create `desktop/electron/credentialStore.ts`:

```ts
import keytar from "keytar";
import type { CredentialStatus, ModelProvider } from "./ipcTypes.js";

const SERVICE = "CodeCub Model API";

export function credentialAccount(provider: ModelProvider): string {
  return `codecub:${provider}:api-key`;
}

export function maskApiKey(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "not configured";
  }
  const suffix = trimmed.slice(-4);
  return `saved ending ${suffix}`;
}

export async function readApiKey(provider: ModelProvider): Promise<string> {
  return (await keytar.getPassword(SERVICE, credentialAccount(provider))) ?? "";
}

export async function saveApiKey(provider: ModelProvider, apiKey: string): Promise<CredentialStatus> {
  const trimmed = apiKey.trim();
  if (!trimmed) {
    await clearApiKey(provider);
    return { configured: false, source: "none", displayHint: "not configured" };
  }
  await keytar.setPassword(SERVICE, credentialAccount(provider), trimmed);
  return { configured: true, source: "secure-store", displayHint: maskApiKey(trimmed) };
}

export async function clearApiKey(provider: ModelProvider): Promise<CredentialStatus> {
  await keytar.deletePassword(SERVICE, credentialAccount(provider));
  return { configured: false, source: "none", displayHint: "not configured" };
}

export async function apiKeyStatus(provider: ModelProvider): Promise<CredentialStatus> {
  const saved = await readApiKey(provider);
  if (saved) {
    return { configured: true, source: "secure-store", displayHint: maskApiKey(saved) };
  }
  const envName = provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY";
  if (process.env[envName]) {
    return { configured: true, source: "environment", displayHint: envName };
  }
  return { configured: false, source: "none", displayHint: "not configured" };
}
```

- [ ] **Step 2: Add credential store tests with mocked keytar**

Create `desktop/tests/credentialStore.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

const passwords = new Map<string, string>();

vi.mock("keytar", () => ({
  default: {
    getPassword: vi.fn(async (_service: string, account: string) => passwords.get(account) ?? null),
    setPassword: vi.fn(async (_service: string, account: string, password: string) => {
      passwords.set(account, password);
    }),
    deletePassword: vi.fn(async (_service: string, account: string) => passwords.delete(account)),
  },
}));

describe("credential store", () => {
  beforeEach(() => {
    passwords.clear();
    delete process.env.OPENAI_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
  });

  it("stores and reads API keys through keytar without exposing plaintext in status", async () => {
    const store = await import("../electron/credentialStore");
    const status = await store.saveApiKey("openai", "sk-test-123456");
    expect(status).toEqual({
      configured: true,
      source: "secure-store",
      displayHint: "saved ending 3456",
    });
    expect(await store.readApiKey("openai")).toBe("sk-test-123456");
    expect(JSON.stringify(status)).not.toContain("sk-test");
  });

  it("reports environment fallback without reading it into app settings", async () => {
    process.env.OPENAI_API_KEY = "sk-env-secret";
    const store = await import("../electron/credentialStore");
    await expect(store.apiKeyStatus("openai")).resolves.toEqual({
      configured: true,
      source: "environment",
      displayHint: "OPENAI_API_KEY",
    });
  });

  it("clears saved API keys", async () => {
    const store = await import("../electron/credentialStore");
    await store.saveApiKey("anthropic", "sk-anthropic");
    await expect(store.readApiKey("anthropic")).resolves.toBe("sk-anthropic");
    await expect(store.clearApiKey("anthropic")).resolves.toEqual({
      configured: false,
      source: "none",
      displayHint: "not configured",
    });
    await expect(store.readApiKey("anthropic")).resolves.toBe("");
  });
});
```

- [ ] **Step 3: Run focused test**

Run:

```powershell
npm run test -- credentialStore.test.ts
```

Expected: credential store tests pass and no plaintext key appears in returned statuses.

### Task 5: Build Backend Launch Configuration

**Files:**

- Create: `desktop/electron/backendLaunchConfig.ts`
- Modify: `desktop/electron/backendProcess.ts`
- Test: `desktop/tests/backendLaunchConfig.test.ts`

- [ ] **Step 1: Back up backend process file**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-backend-launch"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\electron\backendProcess.ts" -Destination (Join-Path $backup "desktop_electron_backendProcess.ts")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Create pure launch helper**

Create `desktop/electron/backendLaunchConfig.ts`:

```ts
import type { AppSettings } from "./ipcTypes.js";

export type BackendLaunchConfig = {
  args: string[];
  env: NodeJS.ProcessEnv;
};

export function buildBackendLaunchConfig(
  projectPath: string,
  settings: AppSettings,
  apiKey: string,
  baseEnv: NodeJS.ProcessEnv = process.env,
): BackendLaunchConfig {
  const args = ["--app-mode", "--cwd", projectPath, "--approval", settings.approvalPolicy];
  const env: NodeJS.ProcessEnv = { ...baseEnv };
  const provider = settings.provider.provider;

  args.push("--provider", provider);
  if (settings.provider.model.trim()) {
    args.push("--model", settings.provider.model.trim());
  }

  if (provider === "ollama") {
    if (settings.provider.host.trim()) {
      args.push("--host", settings.provider.host.trim());
    }
    return { args, env };
  }

  if (settings.provider.baseUrl.trim()) {
    args.push("--base-url", settings.provider.baseUrl.trim());
  }

  if (apiKey.trim()) {
    if (provider === "anthropic") {
      env.ANTHROPIC_API_KEY = apiKey.trim();
    } else {
      env.OPENAI_API_KEY = apiKey.trim();
    }
  }

  return { args, env };
}
```

- [ ] **Step 3: Update backend process to use launch config**

In `desktop/electron/backendProcess.ts`, change `start` signature:

```ts
start(projectPath: string, launchConfig: BackendLaunchConfig): void {
```

Use `launchConfig.args` instead of hardcoded `["--app-mode", "--cwd", ...]`. Keep the existing command selection, but construct command args like:

```ts
const args = hasBundledBackend
  ? launchConfig.args
  : process.env.CODECUB_BACKEND_COMMAND
    ? ["-m", "codecub", ...launchConfig.args]
    : ["run", "python", "-m", "codecub", ...launchConfig.args];
```

Use `launchConfig.env` in `spawn`:

```ts
this.child = spawn(command, args, {
  cwd: this.repoRoot,
  env: launchConfig.env,
  shell: false,
});
```

- [ ] **Step 4: Add launch helper tests**

Create `desktop/tests/backendLaunchConfig.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildBackendLaunchConfig } from "../electron/backendLaunchConfig";
import type { AppSettings } from "../electron/ipcTypes";

function settings(provider: AppSettings["provider"]["provider"]): AppSettings {
  return {
    language: "zh-CN",
    approvalPolicy: "ask",
    provider: {
      provider,
      model: provider === "ollama" ? "qwen3.5:4b" : "qwen-flash",
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      host: "http://127.0.0.1:11434",
      credential: { configured: true, source: "secure-store", displayHint: "saved" },
    },
  };
}

describe("backend launch config", () => {
  it("passes OpenAI-compatible provider settings and API key through env", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("openai"), "sk-secret", {});
    expect(config.args).toEqual([
      "--app-mode",
      "--cwd",
      "D:/repo",
      "--approval",
      "ask",
      "--provider",
      "openai",
      "--model",
      "qwen-flash",
      "--base-url",
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ]);
    expect(config.env.OPENAI_API_KEY).toBe("sk-secret");
    expect(config.env.ANTHROPIC_API_KEY).toBeUndefined();
  });

  it("passes Anthropic-compatible API key through the anthropic env name", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("anthropic"), "sk-anthropic", {});
    expect(config.args).toContain("anthropic");
    expect(config.env.ANTHROPIC_API_KEY).toBe("sk-anthropic");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
  });

  it("does not inject API key for Ollama", () => {
    const config = buildBackendLaunchConfig("D:/repo", settings("ollama"), "sk-ignored", {});
    expect(config.args).toContain("--host");
    expect(config.args).toContain("http://127.0.0.1:11434");
    expect(config.env.OPENAI_API_KEY).toBeUndefined();
    expect(config.env.ANTHROPIC_API_KEY).toBeUndefined();
  });
});
```

- [ ] **Step 5: Run focused test**

Run:

```powershell
npm run test -- backendLaunchConfig.test.ts
```

Expected: launch config tests pass.

### Task 6: Wire Main IPC and Preload API

**Files:**

- Modify: `desktop/electron/main.ts`
- Modify: `desktop/electron/preload.cts`
- Modify: `desktop/electron/ipcTypes.ts`

- [ ] **Step 1: Back up main and preload files**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-ipc-wiring"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\electron\main.ts" -Destination (Join-Path $backup "desktop_electron_main.ts")
Copy-Item -LiteralPath ".\desktop\electron\preload.cts" -Destination (Join-Path $backup "desktop_electron_preload.cts")
Copy-Item -LiteralPath ".\desktop\electron\ipcTypes.ts" -Destination (Join-Path $backup "desktop_electron_ipcTypes.ts")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Add credential-safe IPC methods**

In `desktop/electron/preload.cts`, add:

```ts
saveProviderSettings: (request: SaveProviderSettingsRequest): Promise<AppSettings> =>
  ipcRenderer.invoke("settings:provider-save", request),
clearProviderCredential: (provider: ModelProvider): Promise<AppSettings> =>
  ipcRenderer.invoke("settings:provider-clear-credential", provider),
```

Import the new types from `ipcTypes.js`.

- [ ] **Step 3: Implement main handlers**

In `desktop/electron/main.ts`, import:

```ts
import { buildBackendLaunchConfig } from "./backendLaunchConfig.js";
import { apiKeyStatus, clearApiKey, readApiKey, saveApiKey } from "./credentialStore.js";
import type { BackendCommand, ModelProvider, SaveProviderSettingsRequest, TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "./ipcTypes.js";
```

Update `backend:start` handler:

```ts
ipcMain.handle("backend:start", async (_event, projectPath: string, approvalPolicy: "ask" | "auto" | "never" = "ask") => {
  const settings = await loadSettings();
  const effectiveSettings = { ...settings, approvalPolicy };
  const apiKey = await readApiKey(effectiveSettings.provider.provider);
  backend.start(projectPath, buildBackendLaunchConfig(projectPath, effectiveSettings, apiKey));
  await rememberProject(projectPath);
});
```

Add provider settings handlers:

```ts
ipcMain.handle("settings:provider-save", async (_event, request: SaveProviderSettingsRequest) => {
  const current = await loadSettings();
  let credential = current.provider.credential;
  if (request.clearApiKey) {
    credential = await clearApiKey(request.provider);
  } else if (request.apiKey && request.apiKey.trim()) {
    credential = await saveApiKey(request.provider, request.apiKey);
  } else {
    credential = await apiKeyStatus(request.provider);
  }
  return saveSettings({
    ...current,
    provider: {
      provider: request.provider,
      model: request.model,
      baseUrl: request.baseUrl,
      host: request.host,
      credential,
    },
  });
});

ipcMain.handle("settings:provider-clear-credential", async (_event, provider: ModelProvider) => {
  const current = await loadSettings();
  const credential = await clearApiKey(provider);
  return saveSettings({
    ...current,
    provider: {
      ...current.provider,
      provider,
      credential,
    },
  });
});
```

- [ ] **Step 4: Ensure load settings refreshes credential status**

Update `settings:load` handler so it merges runtime credential status:

```ts
ipcMain.handle("settings:load", async () => {
  const settings = await loadSettings();
  const credential = await apiKeyStatus(settings.provider.provider);
  return saveSettings({
    ...settings,
    provider: { ...settings.provider, credential },
  });
});
```

Expected behavior: renderer receives only status metadata, never plaintext key.

- [ ] **Step 5: Run typecheck**

Run:

```powershell
npm run typecheck
```

Expected: remaining failures are only renderer settings page/App usage if those have not been updated yet.

### Task 7: Update Settings UI

**Files:**

- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/components/SettingsPage.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Modify: `desktop/src/styles/app.css`
- Test: `desktop/tests/SettingsPage.test.tsx`

- [ ] **Step 1: Back up renderer files**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "E:\codex_backup\$stamp-codecub-model-api-settings-ui"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath ".\desktop\src\App.tsx" -Destination (Join-Path $backup "desktop_src_App.tsx")
Copy-Item -LiteralPath ".\desktop\src\components\SettingsPage.tsx" -Destination (Join-Path $backup "desktop_src_components_SettingsPage.tsx")
Copy-Item -LiteralPath ".\desktop\src\i18n\zh-CN.ts" -Destination (Join-Path $backup "desktop_src_i18n_zh-CN.ts")
Copy-Item -LiteralPath ".\desktop\src\i18n\en-US.ts" -Destination (Join-Path $backup "desktop_src_i18n_en-US.ts")
Copy-Item -LiteralPath ".\desktop\src\styles\app.css" -Destination (Join-Path $backup "desktop_src_styles_app.css")
Write-Output $backup
```

Expected: prints the backup folder path.

- [ ] **Step 2: Add app state for provider settings**

In `desktop/src/App.tsx`, add:

```ts
const [providerSettings, setProviderSettings] = useState<AppSettings["provider"] | null>(null);
```

Import `AppSettings` from a renderer-safe type path. If importing from `../electron/ipcTypes` is already accepted by the bundler, use:

```ts
import type { AppSettings } from "../electron/ipcTypes";
```

When loading settings:

```ts
window.codecub.loadSettings().then((settings) => {
  setLocale(settings.language);
  setApprovalPolicy(settings.approvalPolicy);
  setProviderSettings(settings.provider);
});
```

Pass `providerSettings` and `setProviderSettings` to `SettingsPage`.

- [ ] **Step 3: Replace read-only API key source UI**

In `desktop/src/components/SettingsPage.tsx`, add props:

```ts
providerSettings: AppSettings["provider"] | null;
setProviderSettings: Dispatch<SetStateAction<AppSettings["provider"] | null>>;
```

Add local API key state:

```ts
const [apiKey, setApiKey] = useState("");
const settings = providerSettings ?? defaultRendererProviderSettings;
```

Add controls:

- Provider `<select>` with `openai`, `anthropic`, `ollama`.
- Model `<input>`.
- Base URL `<input>` for non-Ollama providers.
- Host `<input>` for Ollama.
- API Key `<input type="password">` for non-Ollama providers.
- Credential status text from `settings.credential.displayHint`.
- Save button.
- Clear API key button for non-Ollama providers.

Save handler:

```ts
const saved = await window.codecub.saveProviderSettings({
  provider: settings.provider,
  model: settings.model,
  baseUrl: settings.baseUrl,
  host: settings.host,
  apiKey,
});
setLocale(saved.language);
setApprovalPolicy(saved.approvalPolicy);
setProviderSettings(saved.provider);
setApiKey("");
```

Clear handler:

```ts
const saved = await window.codecub.clearProviderCredential(settings.provider);
setProviderSettings(saved.provider);
setApiKey("");
```

Keep language and approval save path working. Either save all settings through `saveSettings` plus provider through `saveProviderSettings`, or make `saveProviderSettings` preserve language/approval by using `loadSettings()` current state in main.

- [ ] **Step 4: Add i18n keys**

Add Chinese keys:

```ts
modelApiSettings: "模型 API 设置",
providerOpenAI: "OpenAI 兼容",
providerAnthropic: "Anthropic 兼容",
providerOllama: "Ollama",
apiKey: "API Key",
apiKeyPlaceholder: "输入新的 API Key，保存后不会再次显示",
credentialStatus: "凭据状态",
clearApiKey: "清除 API Key",
notRequiredForOllama: "Ollama 不需要 API Key",
secureStore: "系统安全存储",
```

Add matching English keys:

```ts
modelApiSettings: "Model API Settings",
providerOpenAI: "OpenAI Compatible",
providerAnthropic: "Anthropic Compatible",
providerOllama: "Ollama",
apiKey: "API Key",
apiKeyPlaceholder: "Enter a new API key; it will not be shown again after saving",
credentialStatus: "Credential Status",
clearApiKey: "Clear API Key",
notRequiredForOllama: "Ollama does not require an API key",
secureStore: "System secure storage",
```

- [ ] **Step 5: Add SettingsPage test**

Create `desktop/tests/SettingsPage.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsPage } from "../src/components/SettingsPage";
import { t } from "../src/i18n";
import type { AppSettings } from "../electron/ipcTypes";

const provider: AppSettings["provider"] = {
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  host: "http://127.0.0.1:11434",
  credential: { configured: true, source: "secure-store", displayHint: "saved ending 1234" },
};

describe("SettingsPage", () => {
  it("renders API key input as password and clears it after saving", async () => {
    const saveProviderSettings = vi.fn(async () => ({
      language: "zh-CN",
      approvalPolicy: "ask",
      provider,
    }));
    window.codecub = {
      ...window.codecub,
      saveProviderSettings,
      clearProviderCredential: vi.fn(),
    };

    render(
      <SettingsPage
        locale="zh-CN"
        setLocale={vi.fn()}
        approvalPolicy="ask"
        setApprovalPolicy={vi.fn()}
        providerSettings={provider}
        setProviderSettings={vi.fn()}
        t={(key) => t("zh-CN", key)}
        onBack={vi.fn()}
      />,
    );

    const apiKeyInput = screen.getByLabelText("API Key");
    expect(apiKeyInput).toHaveAttribute("type", "password");
    fireEvent.change(apiKeyInput, { target: { value: "sk-secret-value" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByDisplayValue("");
    expect(saveProviderSettings).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: "sk-secret-value", provider: "openai" }),
    );
  });
});
```

- [ ] **Step 6: Run renderer test**

Run:

```powershell
npm run test -- SettingsPage.test.tsx
```

Expected: test passes. If existing i18n Chinese strings are mojibake in the file encoding, preserve existing file encoding unless separately approved to normalize i18n files.

### Task 8: Add Secret Persistence and Redaction Verification

**Files:**

- Test: `desktop/tests/modelApiSecretSafety.test.ts`

- [ ] **Step 1: Create secret safety regression test**

Create `desktop/tests/modelApiSecretSafety.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { sanitizeSettingsForDisk, defaultSettings } from "../electron/appConfig";

describe("model API secret safety", () => {
  it("does not expose full API keys through settings JSON", () => {
    const secret = "sk-live-dangerous-secret";
    const settings = sanitizeSettingsForDisk({
      ...defaultSettings,
      provider: {
        provider: "openai",
        model: "qwen-flash",
        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        host: "http://127.0.0.1:11434",
        credential: {
          configured: true,
          source: "secure-store",
          displayHint: "saved ending cret",
        },
      },
    });
    const payload = JSON.stringify(settings);
    expect(payload).not.toContain(secret);
    expect(payload).not.toContain("sk-live-dangerous");
    expect(payload).not.toContain("apiKey");
  });
});
```

- [ ] **Step 2: Run secret scan**

Run:

```powershell
rg -n "sk-live-dangerous|sk-secret-value|apiKey\\s*:" .\desktop .\.codecub -g "!desktop\node_modules/**" -g "!desktop\release/**"
```

Expected:

- Test fixture strings may appear in test files only.
- No source path that writes appData settings contains a persisted `apiKey` field.

### Task 9: Full Verification and Packaging

**Files:**

- No planned source edits unless verification exposes a blocker.

- [ ] **Step 1: Run desktop test suite**

Run:

```powershell
npm run test
```

Working directory:

```text
D:\代码备份\pico\pico-main\desktop
```

Expected: all Vitest tests pass.

- [ ] **Step 2: Run desktop typecheck**

Run:

```powershell
npm run typecheck
```

Expected: both renderer and Electron TypeScript projects pass.

- [ ] **Step 3: Run Python regression tests**

Run from repo root:

```powershell
uv run pytest
```

Expected: existing backend tests pass. If unrelated environment failures appear, stop and report exact failing tests.

- [ ] **Step 4: Run Windows packaging**

Run:

```powershell
npm run package:win
```

Working directory:

```text
D:\代码备份\pico\pico-main\desktop
```

Expected:

- `desktop/release/win-unpacked/CodeCub.exe` is regenerated.
- `desktop/release/CodeCub-0.1.0-x64.exe` is regenerated.
- Native `keytar` packaging succeeds.

- [ ] **Step 5: Run packaged smoke**

Run:

```powershell
.\scripts\smoke-packaged.ps1
```

Working directory:

```text
D:\代码备份\pico\pico-main\desktop
```

Expected:

```text
packaged_alive_after_8s=True
```

- [ ] **Step 6: Verify packaged renderer state**

Run the existing remote-debugging DOM check pattern used in the blank-window hotfix and verify:

- Page title is `CodeCub`.
- `window.codecub` exists.
- Settings page can be reached.
- No renderer console error reports missing `keytar`, missing preload API, or failed settings load.

Expected: no renderer errors.

### Task 10: Commit in Small, Auditable Units

**Files:**

- Only stage files modified by this feature.
- Do not stage unrelated icon changes currently visible in the worktree unless the user explicitly asks.

- [ ] **Step 1: Commit dependency and core settings model**

Run:

```powershell
git add desktop/package.json desktop/package-lock.json desktop/electron/ipcTypes.ts desktop/electron/appConfig.ts desktop/tests/appConfig.test.ts
git commit -m "Add non-secret provider settings model"
```

- [ ] **Step 2: Commit secure credential storage**

Run:

```powershell
git add desktop/electron/credentialStore.ts desktop/tests/credentialStore.test.ts
git commit -m "Store model API keys in secure storage"
```

- [ ] **Step 3: Commit backend launch wiring**

Run:

```powershell
git add desktop/electron/backendLaunchConfig.ts desktop/electron/backendProcess.ts desktop/electron/main.ts desktop/electron/preload.cts desktop/tests/backendLaunchConfig.test.ts
git commit -m "Launch backend with configured provider settings"
```

- [ ] **Step 4: Commit settings UI**

Run:

```powershell
git add desktop/src/App.tsx desktop/src/components/SettingsPage.tsx desktop/src/i18n/zh-CN.ts desktop/src/i18n/en-US.ts desktop/src/styles/app.css desktop/tests/SettingsPage.test.tsx desktop/tests/modelApiSecretSafety.test.ts
git commit -m "Add desktop model API settings UI"
```

## Known Risks and Stop Conditions

- `keytar` is a native dependency. If it fails to install, build, or package under Electron 39 on Windows, stop and report the exact install/build error before switching to another storage strategy.
- If packaged `keytar` fails at runtime, stop and create a repair plan for native module packaging before changing the storage model.
- If UI tests expose missing renderer type declarations for new preload APIs, fix the preload global declaration before continuing.
- If any secret value appears in appData, `.codecub/`, trace/report JSON, run logs, or renderer-visible IPC response, stop and repair the data boundary before continuing.
- Do not normalize mojibake or rewrite i18n files broadly unless the user explicitly approves that separate cleanup.

## Plan Self-Review

- Requirement coverage: settings UI, provider/model/base URL/host, API key save/update/clear, OS-backed storage, backend launch wiring, secret non-persistence, and verification are all covered.
- Scope check: this plan is one cohesive subsystem: desktop model API configuration. It does not include provider-native streaming, account sync, public release hardening, or unrelated UI redesign.
- Type consistency: `ModelProvider`, `CredentialStatus`, `ProviderSettings`, `SaveProviderSettingsRequest`, and `AppSettings["provider"]` are defined once in `desktop/electron/ipcTypes.ts` and reused throughout the plan.
- Secret boundary: plaintext API keys enter only renderer input state, IPC request payload, keytar save, and backend launch env at runtime. They are not returned from load/save settings and are not written into appData.
- Dirty worktree risk: existing icon and `desktop/index.html` modifications are unrelated and must not be staged with this feature unless separately approved.
