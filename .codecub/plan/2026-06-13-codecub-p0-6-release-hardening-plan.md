# CodeCub P0.6 Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the completed CodeCub P0 desktop baseline so it is safer to package, inspect, and hand-test before any P1 feature work.

**Architecture:** Keep P0.6 as a release-hardening pass, not a feature expansion. Upgrade risky desktop dependencies in one controlled batch, add Windows installer metadata without changing the backend protocol, split the terminal bundle only at the renderer boundary, and record repeatable packaged-app validation steps in `.codecub/plan`.

**Tech Stack:** npm, Electron, electron-builder, Vite, Vitest, TypeScript, React, PyInstaller, PowerShell smoke scripts, pytest.

---

## Understood Requirement

P0 is merged into `main` and verified. The next stage should reduce release risk before P1 by addressing:

- `npm audit` vulnerabilities.
- Missing Electron package metadata, installer target, and release packaging details.
- Packaged app validation beyond process-alive smoke.
- Vite bundle size warning caused by eager terminal loading.
- Documentation status so requirements and plans do not drift from implementation.

## Confirmed Scope

In scope:

- Desktop dependency security upgrade in `desktop/package.json` and `desktop/package-lock.json`.
- Electron Builder release metadata and Windows NSIS installer target.
- Optional icon/branding approval gate; no generated icon asset is created unless the user explicitly approves the asset source and path.
- Dynamic import of the xterm terminal package to reduce initial renderer bundle size.
- Repeatable smoke/manual acceptance docs and commands.
- P0.6 execution status updates in `.codecub/plan`.

Out of scope:

- P1 feature work.
- Cloud sync, multi-agent orchestration, marketplace distribution, auto-update, code signing certificate purchase, or notarization.
- Silent automatic `npm audit fix --force`.
- Pushing to remote GitHub without explicit user approval.
- Deleting generated directories such as `desktop/release/`, `desktop/resources/`, or `.uv-cache/`.

## Current Evidence

- Current branch after P0 merge: `main`.
- Node: `v24.14.1`; npm: `11.11.0`.
- P0 validation after merge: Python tests, desktop tests, desktop typecheck, and desktop build passed.
- `npm audit --json` reports 8 vulnerabilities: 6 high and 2 critical.
- Direct vulnerability drivers:
  - `electron <=39.8.4`; minimal safe target: `electron@39.8.5`.
  - `vite` through `esbuild`; safe target reported by npm: `vite@8.0.16`.
  - `vitest` through `vite`, `vite-node`, and `@vitest/mocker`; safe target reported by npm: `vitest@4.1.8`.
  - `concurrently@9.2.1` through `shell-quote`; safe target checked from npm: `concurrently@10.0.3`.
  - `@vitejs/plugin-react@5.2.0` peers with `vite@^4.2.0 || ^5.0.0 || ^6.0.0 || ^7.0.0 || ^8.0.0` and should be upgraded with Vite.
- Current package warning: `@xterm/xterm` pushes the initial Vite renderer bundle to about 542 KB.

## Files And Responsibilities

- Modify `desktop/package.json`: dependency versions, package metadata, packaging scripts.
- Modify `desktop/package-lock.json`: lockfile changes from controlled npm installs.
- Modify `desktop/electron-builder.json`: release target, artifact naming, NSIS installer settings, optional icon path after approval.
- Modify `desktop/src/components/TerminalPanel.tsx`: load `@xterm/xterm` only when the user starts the terminal.
- Create `desktop/scripts/smoke-packaged.ps1`: repeatable packaged-app smoke command.
- Create `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md`: manual release checklist.
- Modify `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`: append P0.6 release-hardening status after execution.
- Modify `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`: append P0.6 as the post-P0 hardening stage after execution.
- Modify this plan file after execution: append exact validation results and unresolved risks.

## Backup Requirement

Before modifying any existing file, create a timestamped backup under `E:\codex_backup`.

Use this backup shape for P0.6:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-release-hardening"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'desktop\package.json') -Destination (Join-Path $backup 'desktop_package.json')
Copy-Item -LiteralPath (Join-Path $repo 'desktop\package-lock.json') -Destination (Join-Path $backup 'desktop_package-lock.json')
Copy-Item -LiteralPath (Join-Path $repo 'desktop\electron-builder.json') -Destination (Join-Path $backup 'desktop_electron-builder.json')
Copy-Item -LiteralPath (Join-Path $repo 'desktop\src\components\TerminalPanel.tsx') -Destination (Join-Path $backup 'desktop_src_components_TerminalPanel.tsx')
Copy-Item -LiteralPath (Join-Path $repo '.codecub\plan\2026-06-11-codecub-p0-acceptance-checklist.md') -Destination (Join-Path $backup 'codecub_plan_p0_acceptance_checklist.md')
Copy-Item -LiteralPath (Join-Path $repo '.codecub\plan\2026-06-11-codecub-p0-master-implementation-plan.md') -Destination (Join-Path $backup 'codecub_plan_p0_master_implementation_plan.md')
Write-Output $backup
```

New files do not need backups.

## Approval Gates

Stop and ask the user before:

- Installing or upgrading npm packages.
- Creating or downloading an icon/image asset.
- Deleting generated build output.
- Pushing `main` to the remote.
- Changing scope from release hardening into P1 features.

## Stop Conditions

Stop, report, and write a repair plan if:

- Dependency upgrades make Electron, Vite, Vitest, or TypeScript incompatible.
- `npm audit --audit-level=high` still reports high or critical vulnerabilities after the controlled upgrade.
- `node-pty` fails in packaged smoke after Electron upgrade.
- NSIS packaging fails because of environment or signing assumptions.
- Dynamic terminal import breaks terminal startup, terminal output, or cwd behavior.
- Full Python or desktop regression fails after one scoped repair attempt.

---

### Task 1: Dependency Security Upgrade

**Files:**
- Modify: `desktop/package.json`
- Modify: `desktop/package-lock.json`

- [ ] **Step 1: Ask for dependency-upgrade approval**

Ask the user:

```text
P0.6 needs to upgrade desktop dev dependencies inside D:\代码备份\pico\pico-main\desktop to address npm audit findings.
Approve running npm install --save-dev electron@39.8.5 vite@8.0.16 vitest@4.1.8 @vitejs/plugin-react@5.2.0 concurrently@10.0.3?
```

Expected: wait for explicit confirmation.

- [ ] **Step 2: Back up package metadata**

Run from repo root:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-dependency-upgrade"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'desktop\package.json') -Destination (Join-Path $backup 'desktop_package.json')
Copy-Item -LiteralPath (Join-Path $repo 'desktop\package-lock.json') -Destination (Join-Path $backup 'desktop_package-lock.json')
Write-Output $backup
```

Expected: backup path is printed.

- [ ] **Step 3: Upgrade the controlled dependency set**

Run:

```powershell
cd desktop
npm install --save-dev electron@39.8.5 vite@8.0.16 vitest@4.1.8 @vitejs/plugin-react@5.2.0 concurrently@10.0.3
```

Expected: command exits 0 and updates only `desktop/package.json` plus `desktop/package-lock.json`.

- [ ] **Step 4: Verify audit result**

Run:

```powershell
cd desktop
npm audit --audit-level=high
```

Expected: exits 0 and reports no high or critical vulnerabilities.

- [ ] **Step 5: Verify desktop compatibility**

Run:

```powershell
cd desktop
npm test
npm run typecheck
npm run build
```

Expected:

```text
Test Files  7 passed
Tests  13 passed
```

Typecheck and build both exit 0.

- [ ] **Step 6: Verify packaging compatibility after Electron upgrade**

Run:

```powershell
uv run python scripts/package_backend.py
cd desktop
npm run package:win
```

Expected:

```text
desktop\resources\backend\codecub-agent.exe
desktop\release\win-unpacked\CodeCub.exe
```

Both files exist after the commands finish.

---

### Task 2: Release Metadata And Windows Installer Target

**Files:**
- Modify: `desktop/package.json`
- Modify: `desktop/electron-builder.json`

- [ ] **Step 1: Back up packaging config**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-packaging-metadata"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'desktop\package.json') -Destination (Join-Path $backup 'desktop_package.json')
Copy-Item -LiteralPath (Join-Path $repo 'desktop\electron-builder.json') -Destination (Join-Path $backup 'desktop_electron-builder.json')
Write-Output $backup
```

- [ ] **Step 2: Add package metadata and scripts**

Update `desktop/package.json` so the top section is:

```json
{
  "name": "codecub-desktop",
  "version": "0.1.0",
  "description": "CodeCub desktop coding agent",
  "author": "CodeCub",
  "private": true,
  "type": "module",
  "main": "dist-electron/main.js",
  "scripts": {
    "dev": "concurrently -k \"vite --host 127.0.0.1\" \"tsc -p tsconfig.node.json --watch\" \"wait-on http://127.0.0.1:5173 && electron dist-electron/main.js\"",
    "build": "tsc -p tsconfig.json && tsc -p tsconfig.node.json && vite build",
    "package:win": "npm run build && electron-builder --config electron-builder.json --win",
    "package:win:dir": "npm run build && electron-builder --config electron-builder.json --win dir",
    "package:win:installer": "npm run build && electron-builder --config electron-builder.json --win nsis",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc -p tsconfig.json && tsc -p tsconfig.node.json"
  }
}
```

Keep the existing dependency and devDependency blocks after this section, with the upgraded versions from Task 1.

- [ ] **Step 3: Add NSIS release configuration**

Update `desktop/electron-builder.json` to:

```json
{
  "appId": "com.codecub.desktop",
  "productName": "CodeCub",
  "artifactName": "CodeCub-${version}-${arch}.${ext}",
  "directories": {
    "output": "release"
  },
  "npmRebuild": false,
  "asarUnpack": [
    "node_modules/node-pty/prebuilds/**/*"
  ],
  "files": [
    "dist-electron/**/*",
    "dist-renderer/**/*",
    "package.json"
  ],
  "extraResources": [
    {
      "from": "resources/backend/codecub-agent.exe",
      "to": "backend/codecub-agent.exe"
    }
  ],
  "win": {
    "target": [
      {
        "target": "dir",
        "arch": ["x64"]
      },
      {
        "target": "nsis",
        "arch": ["x64"]
      }
    ]
  },
  "nsis": {
    "oneClick": false,
    "perMachine": false,
    "allowToChangeInstallationDirectory": true,
    "createDesktopShortcut": true,
    "createStartMenuShortcut": true,
    "shortcutName": "CodeCub"
  }
}
```

- [ ] **Step 4: Build both package forms**

Run:

```powershell
cd desktop
npm run package:win
```

Expected:

```text
desktop\release\win-unpacked\CodeCub.exe
desktop\release\CodeCub-0.1.0-x64.exe
```

If the installer artifact name differs only by electron-builder suffix while still producing an `.exe` installer under `desktop\release`, record the exact filename in the P0.6 execution status.

---

### Task 3: Icon And Pet Branding Gate

**Files:**
- Optional create after approval: `desktop/build/icon.ico`
- Optional create after approval: `desktop/build/icon.svg`
- Optional modify after approval: `desktop/electron-builder.json`

- [ ] **Step 1: Ask for icon source approval**

Ask the user:

```text
P0.6 can keep the default Electron icon, use a user-provided icon file, or create a simple in-repo CodeCub pet icon under desktop/build/.
Which icon source do you approve?
```

Expected: wait for explicit answer.

- [ ] **Step 2A: If the user chooses default icon**

Do not create icon files. Add this line to `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md` in Task 5:

```markdown
- Icon status: default Electron icon retained by explicit user choice.
```

- [ ] **Step 2B: If the user provides an `.ico` path**

Copy only the approved file into:

```text
desktop/build/icon.ico
```

Then add to `desktop/electron-builder.json` inside `win`:

```json
"icon": "build/icon.ico"
```

Expected: the file exists and `npm run package:win` uses it without warning about the default Electron icon.

- [ ] **Step 2C: If the user approves generated local icon**

Stop and create a separate icon-generation mini-plan before writing binary or image assets. The mini-plan must specify:

- Exact files under `desktop/build/`.
- Generation method.
- Verification command.
- Whether any tool or dependency is required.

No image or icon file is generated during this task without that mini-plan being approved.

---

### Task 4: Lazy Load Terminal Bundle

**Files:**
- Modify: `desktop/src/components/TerminalPanel.tsx`

- [ ] **Step 1: Back up terminal panel**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-terminal-lazy-load"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'desktop\src\components\TerminalPanel.tsx') -Destination (Join-Path $backup 'desktop_src_components_TerminalPanel.tsx')
Write-Output $backup
```

- [ ] **Step 2: Replace eager xterm imports**

In `desktop/src/components/TerminalPanel.tsx`, remove these eager imports:

```ts
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
```

Add this type alias below the React imports:

```ts
type XTermInstance = import("@xterm/xterm").Terminal;
```

Update the terminal ref:

```ts
const terminalRef = useRef<XTermInstance | null>(null);
```

Update `startTerminal` so the first lines inside the function are:

```ts
async function startTerminal() {
  if (!containerRef.current || running) {
    return;
  }
  await import("@xterm/xterm/css/xterm.css");
  const { Terminal } = await import("@xterm/xterm");
  const terminal = new Terminal({ cols: 100, rows: 24, cursorBlink: true });
```

Keep the rest of the function behavior unchanged.

- [ ] **Step 3: Verify bundle split and behavior**

Run:

```powershell
cd desktop
npm run typecheck
npm run build
```

Expected:

- Typecheck exits 0.
- Build exits 0.
- The initial `assets/index-*.js` file is below 500 KB, or the P0.6 execution status records the exact remaining Vite warning.

- [ ] **Step 4: Verify terminal tests**

Run:

```powershell
cd desktop
npm test -- terminalBridge terminalIpcTypes
```

Expected:

```text
Test Files  2 passed
Tests  3 passed
```

---

### Task 5: Packaged Smoke Script And Release Checklist

**Files:**
- Create: `desktop/scripts/smoke-packaged.ps1`
- Create: `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md`

- [ ] **Step 1: Create packaged smoke script**

Create `desktop/scripts/smoke-packaged.ps1`:

```powershell
$ErrorActionPreference = 'Stop'

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$exe = Join-Path $desktopRoot 'release\win-unpacked\CodeCub.exe'
$backend = Join-Path $desktopRoot 'release\win-unpacked\resources\backend\codecub-agent.exe'

if (-not (Test-Path $exe)) {
  throw "Missing packaged app: $exe"
}

if (-not (Test-Path $backend)) {
  throw "Missing bundled backend: $backend"
}

$process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 8
$alive = -not $process.HasExited
if ($alive) {
  Stop-Process -Id $process.Id -Force
}

Write-Output "packaged_alive_after_8s=$alive"
if (-not $alive) {
  throw 'Packaged app exited before smoke window elapsed.'
}
```

- [ ] **Step 2: Create release checklist**

Create `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md`:

```markdown
# CodeCub P0.6 Release Checklist

## Automated Checks

- `uv run pytest -q -ra --durations=10`
- `uv run python -m codecub --help`
- `uv run python scripts/package_backend.py`
- `desktop/resources/backend/codecub-agent.exe --help`
- `cd desktop && npm audit --audit-level=high`
- `cd desktop && npm test`
- `cd desktop && npm run typecheck`
- `cd desktop && npm run build`
- `cd desktop && npm run package:win`
- `powershell -ExecutionPolicy Bypass -File desktop/scripts/smoke-packaged.ps1`

## Manual Packaged App Checks

- Launch `desktop/release/win-unpacked/CodeCub.exe`.
- Open a real local project folder.
- Confirm the default language is Chinese.
- Confirm Git badge shows branch and dirty/clean state.
- Send a harmless prompt such as `请总结这个项目结构`.
- Confirm backend events appear in the run log.
- Trigger a risky operation in ask mode and confirm the approval dialog appears before execution.
- Reject the operation and confirm no mutation happens.
- Open the terminal and run `pwd` or `Get-Location`.
- Confirm terminal cwd matches the selected project path.
- Close and relaunch the packaged app.

## Release Notes

- Release candidate: CodeCub `0.1.0`.
- Distribution format: Windows unpacked directory and NSIS installer.
- Push status: local only until the user explicitly approves remote push.
```

- [ ] **Step 3: Run packaged smoke script**

Run from repo root:

```powershell
powershell -ExecutionPolicy Bypass -File desktop/scripts/smoke-packaged.ps1
```

Expected:

```text
packaged_alive_after_8s=True
```

---

### Task 6: Final Regression And Documentation Sync

**Files:**
- Modify: `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`
- Modify: `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`
- Modify: `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`

- [ ] **Step 1: Back up existing plan documents**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-doc-status"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo '.codecub\plan\2026-06-11-codecub-p0-acceptance-checklist.md') -Destination (Join-Path $backup 'codecub_plan_p0_acceptance_checklist.md')
Copy-Item -LiteralPath (Join-Path $repo '.codecub\plan\2026-06-11-codecub-p0-master-implementation-plan.md') -Destination (Join-Path $backup 'codecub_plan_p0_master_implementation_plan.md')
Copy-Item -LiteralPath (Join-Path $repo '.codecub\plan\2026-06-13-codecub-p0-6-release-hardening-plan.md') -Destination (Join-Path $backup 'codecub_plan_p0_6_release_hardening_plan.md')
Write-Output $backup
```

- [ ] **Step 2: Run full backend verification**

Run:

```powershell
$env:UV_CACHE_DIR = (Join-Path (Resolve-Path '.').Path '.uv-cache')
uv run pytest -q -ra --durations=10
uv run python -m codecub --help
uv run python scripts/package_backend.py
desktop\resources\backend\codecub-agent.exe --help
```

Expected:

- pytest exits 0.
- `--help` commands exit 0.
- backend exe exists.

- [ ] **Step 3: Run full desktop verification**

Run:

```powershell
cd desktop
npm audit --audit-level=high
npm test
npm run typecheck
npm run build
npm run package:win
cd ..
powershell -ExecutionPolicy Bypass -File desktop/scripts/smoke-packaged.ps1
```

Expected:

- audit exits 0 for high/critical vulnerabilities.
- tests, typecheck, build, package, and packaged smoke all exit 0.

- [ ] **Step 4: Append P0.6 status to acceptance checklist**

Append to `.codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md`:

```markdown

## P0.6 Release Hardening Status

- Dependency audit: completed with `npm audit --audit-level=high`.
- Windows installer: completed with `npm run package:win`.
- Packaged smoke: completed with `desktop/scripts/smoke-packaged.ps1`.
- Terminal bundle: lazy-loaded through dynamic import.
- Remaining release risks are recorded in `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`.
```

- [ ] **Step 5: Append P0.6 status to master plan**

In `.codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md`, add this bullet under current stage status:

```markdown
- P0.6 release hardening: completed and verified in `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`.
```

- [ ] **Step 6: Append execution status to this plan**

First capture the timestamp:

```powershell
Get-Date -Format 'yyyy-MM-dd HH:mm'
```

Append:

```markdown

---

## Execution Status

Completed on the timestamp printed by `Get-Date -Format 'yyyy-MM-dd HH:mm'` in Asia/Shanghai.

Verification:

- `uv run pytest -q -ra --durations=10`: record the exact passed/skipped/warning counts from terminal output.
- `uv run python -m codecub --help`: passed.
- `uv run python scripts/package_backend.py`: passed.
- `desktop/resources/backend/codecub-agent.exe --help`: passed.
- `cd desktop && npm audit --audit-level=high`: passed.
- `cd desktop && npm test`: record the exact passed test-file and test counts from terminal output.
- `cd desktop && npm run typecheck`: passed.
- `cd desktop && npm run build`: passed.
- `cd desktop && npm run package:win`: passed.
- `powershell -ExecutionPolicy Bypass -File desktop/scripts/smoke-packaged.ps1`: `packaged_alive_after_8s=True`.

Remaining risks:

- Remote push is not done until the user explicitly approves it.
- Manual packaged app workflow checks must be marked off by the user before public release.
```

- [ ] **Step 7: Commit P0.6**

Run:

```powershell
git status --short
git add desktop/package.json desktop/package-lock.json desktop/electron-builder.json desktop/src/components/TerminalPanel.tsx desktop/scripts/smoke-packaged.ps1 .codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md .codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md .codecub/plan/2026-06-11-codecub-p0-acceptance-checklist.md .codecub/plan/2026-06-11-codecub-p0-master-implementation-plan.md
git commit -m "Harden CodeCub P0 release packaging"
```

Expected: commit exits 0.

---

## Plan Review

- Requirement match: The plan addresses the release risks identified after P0: npm audit, installer metadata, package target, packaged smoke, bundle warning, and docs sync.
- Scope control: The plan does not add P1 product features and does not push to remote.
- Approval gates: Dependency upgrades and icon assets require explicit user confirmation before execution.
- Maintainability: Dependency upgrades are grouped into one controlled batch; terminal bundle optimization is isolated to `TerminalPanel.tsx`; packaging metadata stays in Electron Builder config.
- Security: The plan avoids `npm audit fix --force` and requires a clean high/critical audit result before considering P0.6 complete.
- Filesystem safety: Existing files are backed up under `E:\codex_backup` before modification; generated directories are not deleted.
- Placeholder scan: The plan contains no unresolved implementation placeholders. Task 6 requires writing exact execution results from terminal output.

## Execution Choice

Recommended execution: inline execution with `superpowers:executing-plans`, because tasks are sequential and each step depends on the result of the dependency upgrade.

---

## Execution Status

Completed on 2026-06-15 15:24 Asia/Shanghai.

Implemented:

- Upgraded desktop dev dependencies to clear npm audit findings.
- Configured Electron Builder release metadata, NSIS installer target, and artifact naming.
- Configured local Electron runtime zip at `desktop/.electron-cache/electron-v39.8.10/electron-v39.8.10-win32-x64.zip` through `electronDist`.
- Generated a local CodeCub pet icon and configured `win.icon`.
- Lazy-loaded `@xterm/xterm` and its CSS from `TerminalPanel.tsx`.
- Added `desktop/scripts/smoke-packaged.ps1`.
- Added `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md`.

Verification:

- `uv run pytest -q -ra --durations=10`: `137 passed, 2 skipped, 6 warnings`.
- `uv run python -m codecub --help`: passed.
- `uv run python scripts/package_backend.py`: passed.
- `desktop/resources/backend/codecub-agent.exe --help`: passed.
- `cd desktop && npm audit --audit-level=high`: `found 0 vulnerabilities`.
- `cd desktop && npm test`: `7 passed` test files / `13 passed` tests.
- `cd desktop && npm run typecheck`: passed.
- `cd desktop && npm run build`: passed; initial renderer JS is `211.67 kB`, with xterm split to `340.34 kB`.
- `cd desktop && npm run package:win`: passed; generated `desktop/release/win-unpacked/CodeCub.exe` and `desktop/release/CodeCub-0.1.0-x64.exe`.
- `powershell -ExecutionPolicy Bypass -File desktop/scripts/smoke-packaged.ps1`: `packaged_alive_after_8s=True`.

Resolved blockers:

- `@vitejs/plugin-react@6.0.2` had peer dependency friction for this project. The plan was corrected to `@vitejs/plugin-react@5.2.0`, which supports Vite 8 without introducing extra peer requirements.
- Electron 39.8.10 download repeatedly timed out from GitHub. The user supplied `D:\浏览器下载\electron-v39.8.10-win32-x64.zip`; it was copied into project-local `.electron-cache/` and wired through `electronDist`.
- Dynamic CSS import needed a CSS module declaration; `desktop/src/vite-env.d.ts` was added.

Remaining risks:

- Remote push is not done until the user explicitly approves it.
- Manual packaged app workflow checks in `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md` must be marked off by the user before public release.
- `desktop/.electron-cache/`, `desktop/release/`, and `desktop/resources/backend/` are local generated/cache outputs and are intentionally ignored by Git.
