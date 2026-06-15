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
Write-Output "packaged_backend=$backend"

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
