# CodeCub P0.6 Pet Icon Mini Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a simple local CodeCub pet icon for the Windows desktop package without downloading assets or adding npm/Python dependencies.

**Architecture:** Use an in-repo PowerShell script to generate deterministic icon files from local drawing commands. The script creates a CodeCub pet-style mascot icon as PNG sizes and a Windows `.ico`, then Electron Builder uses `desktop/build/icon.ico` for packaged Windows output.

**Tech Stack:** PowerShell, .NET `System.Drawing`, Electron Builder, npm package build, Windows smoke tests.

---

## Understood Requirement

The user selected the P0.6 icon option:

- Generate a CodeCub pet icon.
- First write a mini-plan.
- Do not generate the binary/image assets until this plan is explicitly approved.

## Confirmed Scope

In scope:

- Create a deterministic local icon generator script.
- Generate files only under `desktop/build/`.
- Configure Electron Builder to use `desktop/build/icon.ico`.
- Verify the icon files exist and Windows packaging still succeeds.

Out of scope:

- Downloading icon/image assets.
- Calling external image-generation services.
- Installing new dependencies.
- Editing branding across the full UI.
- Creating macOS `.icns` or Linux icon sets.

## Files And Responsibilities

- Create `desktop/scripts/generate-codecub-icon.ps1`: draws the CodeCub pet icon and writes icon files.
- Create after execution `desktop/build/icon.ico`: Windows icon consumed by Electron Builder.
- Create after execution `desktop/build/icon-256.png`: reviewable large preview image.
- Modify `desktop/electron-builder.json`: add `"icon": "build/icon.ico"` under `win`.
- Modify `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md` after it exists: record icon status.
- Modify `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`: append mini-plan execution status after verification.

## Icon Design Contract

The generated icon should be recognizable at small sizes:

- Subject: a compact CodeCub pet mascot.
- Shape: rounded bear/cub head with small ears.
- Expression: focused but friendly.
- Coding signal: small terminal prompt mark or code bracket on the face/body area.
- Palette: not one-note purple/blue; use warm cream face, dark outline, green code accent, and muted amber detail.
- Background: transparent.
- Output sizes in `.ico`: 256, 128, 64, 48, 32, and 16 px.

## Approval Gates

Stop and ask before:

- Running the script that creates `desktop/build/` files.
- Modifying `desktop/electron-builder.json`.
- Deleting or overwriting any existing file under `desktop/build/`.

If `desktop/build/` already contains icon files, back them up before overwriting and ask before deletion. Overwriting approved icon outputs is allowed only after the user confirms this mini-plan execution.

---

### Task 1: Write The Local Icon Generator

**Files:**
- Create: `desktop/scripts/generate-codecub-icon.ps1`

- [ ] **Step 1: Check for existing icon outputs**

Run:

```powershell
Test-Path desktop\build
Get-ChildItem desktop\build -ErrorAction SilentlyContinue
```

Expected:

- If the folder does not exist, continue.
- If icon files exist, stop and ask whether to overwrite them after backing them up.

- [ ] **Step 2: Create the generator script**

Create `desktop/scripts/generate-codecub-icon.ps1`:

```powershell
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$buildDir = Join-Path $desktopRoot 'build'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$sizes = @(256, 128, 64, 48, 32, 16)
$pngFrames = @()

function New-Brush($hex) {
  return New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($hex))
}

function New-Pen($hex, $width) {
  $pen = New-Object System.Drawing.Pen ([System.Drawing.ColorTranslator]::FromHtml($hex), $width)
  $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  return $pen
}

function Draw-CodeCub($size, $path) {
  $bitmap = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.Color]::Transparent)

  $scale = $size / 256.0
  function S($value) { return [int][Math]::Round($value * $scale) }

  $outline = New-Pen '#1f2933' (S 10)
  $thinOutline = New-Pen '#1f2933' (S 7)
  $face = New-Brush '#f6dfb8'
  $muzzle = New-Brush '#fff4dc'
  $ear = New-Brush '#d9a85d'
  $green = New-Brush '#37b26c'
  $amber = New-Brush '#f2b84b'
  $dark = New-Brush '#1f2933'

  $graphics.FillEllipse($ear, (S 42), (S 34), (S 62), (S 62))
  $graphics.DrawEllipse($outline, (S 42), (S 34), (S 62), (S 62))
  $graphics.FillEllipse($ear, (S 152), (S 34), (S 62), (S 62))
  $graphics.DrawEllipse($outline, (S 152), (S 34), (S 62), (S 62))

  $graphics.FillEllipse($face, (S 36), (S 50), (S 184), (S 172))
  $graphics.DrawEllipse($outline, (S 36), (S 50), (S 184), (S 172))

  $graphics.FillEllipse($muzzle, (S 82), (S 122), (S 92), (S 58))
  $graphics.DrawEllipse($thinOutline, (S 82), (S 122), (S 92), (S 58))

  $graphics.FillEllipse($dark, (S 82), (S 104), (S 18), (S 22))
  $graphics.FillEllipse($dark, (S 156), (S 104), (S 18), (S 22))
  $graphics.FillEllipse($dark, (S 119), (S 130), (S 18), (S 14))

  $graphics.DrawLine($thinOutline, (S 108), (S 158), (S 126), (S 166))
  $graphics.DrawLine($thinOutline, (S 126), (S 166), (S 148), (S 158))

  $graphics.FillRectangle($green, (S 66), (S 184), (S 124), (S 32))
  $graphics.DrawRectangle($thinOutline, (S 66), (S 184), (S 124), (S 32))

  $fontSize = [Math]::Max(8, (S 23))
  $font = New-Object System.Drawing.Font('Consolas', $fontSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
  $graphics.DrawString('>_', $font, $dark, (S 84), (S 188))

  $graphics.FillEllipse($amber, (S 184), (S 78), (S 18), (S 18))

  $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $graphics.Dispose()
  $bitmap.Dispose()
}

foreach ($size in $sizes) {
  $pngPath = Join-Path $buildDir "icon-$size.png"
  Draw-CodeCub $size $pngPath
  $pngFrames += Get-Item $pngPath
}

Copy-Item -LiteralPath (Join-Path $buildDir 'icon-256.png') -Destination (Join-Path $buildDir 'icon-preview.png') -Force

$icoPath = Join-Path $buildDir 'icon.ico'
$stream = [System.IO.File]::Create($icoPath)
$writer = New-Object System.IO.BinaryWriter($stream)

$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]$pngFrames.Count)

$offset = 6 + (16 * $pngFrames.Count)
$pngBytes = @()

foreach ($frame in $pngFrames) {
  $bytes = [System.IO.File]::ReadAllBytes($frame.FullName)
  $pngBytes += ,$bytes
  $size = [int]($frame.BaseName -replace 'icon-', '')
  $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
  $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
  $writer.Write([byte]0)
  $writer.Write([byte]0)
  $writer.Write([UInt16]1)
  $writer.Write([UInt16]32)
  $writer.Write([UInt32]$bytes.Length)
  $writer.Write([UInt32]$offset)
  $offset += $bytes.Length
}

foreach ($bytes in $pngBytes) {
  $writer.Write($bytes)
}

$writer.Dispose()
$stream.Dispose()

Get-Item $icoPath, (Join-Path $buildDir 'icon-preview.png') | Select-Object FullName,Length
```

- [ ] **Step 3: Verify script syntax without generating assets**

Run:

```powershell
$null = [scriptblock]::Create((Get-Content desktop\scripts\generate-codecub-icon.ps1 -Raw))
```

Expected: exits 0.

---

### Task 2: Generate Icon Assets

**Files:**
- Create: `desktop/build/icon.ico`
- Create: `desktop/build/icon-preview.png`
- Create: `desktop/build/icon-256.png`
- Create: `desktop/build/icon-128.png`
- Create: `desktop/build/icon-64.png`
- Create: `desktop/build/icon-48.png`
- Create: `desktop/build/icon-32.png`
- Create: `desktop/build/icon-16.png`

- [ ] **Step 1: Run the generator**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File desktop/scripts/generate-codecub-icon.ps1
```

Expected:

- `desktop/build/icon.ico` exists.
- `desktop/build/icon-preview.png` exists.
- Script prints both file paths and non-zero lengths.

- [ ] **Step 2: Inspect generated files**

Run:

```powershell
Get-Item desktop\build\icon.ico, desktop\build\icon-preview.png | Select-Object FullName,Length
```

Expected:

- `icon.ico` length is greater than 1000 bytes.
- `icon-preview.png` length is greater than 1000 bytes.

---

### Task 3: Wire Icon Into Windows Packaging

**Files:**
- Modify: `desktop/electron-builder.json`

- [ ] **Step 1: Back up Electron Builder config**

Run:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repo = (Resolve-Path '.').Path
$backup = "E:\codex_backup\$stamp-codecub-p0-6-icon-config"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'desktop\electron-builder.json') -Destination (Join-Path $backup 'desktop_electron-builder.json')
Write-Output $backup
```

- [ ] **Step 2: Add icon to Windows config**

In `desktop/electron-builder.json`, update the `win` block to include:

```json
"icon": "build/icon.ico"
```

The `win` block should keep the existing `target` entries.

- [ ] **Step 3: Verify package build uses the icon**

Run:

```powershell
cd desktop
npm run package:win
```

Expected:

- Command exits 0.
- Output does not include `default Electron icon is used`.
- `desktop/release/win-unpacked/CodeCub.exe` exists.
- `desktop/release/CodeCub-0.1.0-x64.exe` exists.

---

### Task 4: Document Icon Status

**Files:**
- Modify after it exists: `.codecub/plan/2026-06-13-codecub-p0-6-release-checklist.md`
- Modify: `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`

- [ ] **Step 1: Record icon status**

Add this line to the P0.6 release checklist:

```markdown
- Icon status: generated local CodeCub pet icon from `desktop/scripts/generate-codecub-icon.ps1`; packaged through `desktop/build/icon.ico`.
```

- [ ] **Step 2: Append mini-plan execution status**

Append this section to `.codecub/plan/2026-06-15-codecub-p0-6-pet-icon-mini-plan.md`:

```markdown

---

## Execution Status

- `desktop/scripts/generate-codecub-icon.ps1`: created.
- `desktop/build/icon.ico`: generated.
- `desktop/build/icon-preview.png`: generated.
- `desktop/electron-builder.json`: configured with `win.icon`.
- `cd desktop && npm run package:win`: passed without default Electron icon warning.
```

---

## Plan Review

- Requirement match: The plan generates a local CodeCub pet icon and wires it into the Windows package.
- Scope control: No external downloads, no new dependencies, and no P1 feature work.
- Filesystem safety: All generated icon outputs stay under `desktop/build/`; existing files are checked before overwrite.
- Maintainability: The icon is reproducible from a script instead of an opaque binary-only asset.
- Verification: The plan checks file existence, file size, script syntax, and Electron Builder output.
- Placeholder scan: No unresolved implementation placeholders remain.

---

## Execution Status

- `desktop/scripts/generate-codecub-icon.ps1`: created.
- `desktop/build/icon.ico`: generated, 16820 bytes.
- `desktop/build/icon-preview.png`: generated, 7614 bytes.
- `desktop/electron-builder.json`: configured with `win.icon`.
- `cd desktop && npm run package:win`: passed without default Electron icon warning.
