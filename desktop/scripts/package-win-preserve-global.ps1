param(
  [ValidateSet("win", "dir", "installer")]
  [string]$Target = "win"
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$releaseGlobal = Join-Path $desktopRoot "release\win-unpacked\codecub-global"
$preserveRoot = Join-Path $desktopRoot ".package-preserve"
$preservedGlobal = Join-Path $preserveRoot "codecub-global"

function Copy-DirectoryFresh {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Preserve-GlobalConfig {
  if (!(Test-Path -LiteralPath $releaseGlobal)) {
    Write-Host "No existing codecub-global found to preserve."
    return $false
  }

  Copy-DirectoryFresh -Source $releaseGlobal -Destination $preservedGlobal
  Write-Host "Preserved codecub-global before packaging."
  return $true
}

function Restore-GlobalConfig {
  if (!(Test-Path -LiteralPath $preservedGlobal)) {
    return
  }

  Copy-DirectoryFresh -Source $preservedGlobal -Destination $releaseGlobal
  Write-Host "Restored codecub-global after packaging."
}

function Remove-PreserveRoot {
  if (Test-Path -LiteralPath $preserveRoot) {
    Remove-Item -LiteralPath $preserveRoot -Recurse -Force
  }
}

$hadGlobalConfig = Preserve-GlobalConfig

try {
  Push-Location $desktopRoot
  try {
    & npm run build
    if ($LASTEXITCODE -ne 0) {
      throw "npm run build failed with exit code $LASTEXITCODE"
    }

    $electronBuilder = Join-Path $desktopRoot "node_modules\.bin\electron-builder.cmd"
    if (!(Test-Path -LiteralPath $electronBuilder)) {
      throw "electron-builder was not found at $electronBuilder. Run npm install in the desktop folder first."
    }

    $builderArgs = @("--config", "electron-builder.json", "--win")

    if ($Target -eq "dir") {
      $builderArgs += "dir"
    } elseif ($Target -eq "installer") {
      $builderArgs += "nsis"
    }

    & $electronBuilder @builderArgs
    if ($LASTEXITCODE -ne 0) {
      throw "electron-builder failed with exit code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
} finally {
  if ($hadGlobalConfig) {
    Restore-GlobalConfig
  }
  Remove-PreserveRoot
}
