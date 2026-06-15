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

function Scale-Value($value, $scale) {
  return [int][Math]::Round($value * $scale)
}

function Draw-CodeCub($size, $path) {
  $bitmap = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.Color]::Transparent)

  $scale = $size / 256.0

  $outline = New-Pen '#1f2933' (Scale-Value 10 $scale)
  $thinOutline = New-Pen '#1f2933' (Scale-Value 7 $scale)
  $face = New-Brush '#f6dfb8'
  $muzzle = New-Brush '#fff4dc'
  $ear = New-Brush '#d9a85d'
  $green = New-Brush '#37b26c'
  $amber = New-Brush '#f2b84b'
  $dark = New-Brush '#1f2933'

  $graphics.FillEllipse($ear, (Scale-Value 42 $scale), (Scale-Value 34 $scale), (Scale-Value 62 $scale), (Scale-Value 62 $scale))
  $graphics.DrawEllipse($outline, (Scale-Value 42 $scale), (Scale-Value 34 $scale), (Scale-Value 62 $scale), (Scale-Value 62 $scale))
  $graphics.FillEllipse($ear, (Scale-Value 152 $scale), (Scale-Value 34 $scale), (Scale-Value 62 $scale), (Scale-Value 62 $scale))
  $graphics.DrawEllipse($outline, (Scale-Value 152 $scale), (Scale-Value 34 $scale), (Scale-Value 62 $scale), (Scale-Value 62 $scale))

  $graphics.FillEllipse($face, (Scale-Value 36 $scale), (Scale-Value 50 $scale), (Scale-Value 184 $scale), (Scale-Value 172 $scale))
  $graphics.DrawEllipse($outline, (Scale-Value 36 $scale), (Scale-Value 50 $scale), (Scale-Value 184 $scale), (Scale-Value 172 $scale))

  $graphics.FillEllipse($muzzle, (Scale-Value 82 $scale), (Scale-Value 122 $scale), (Scale-Value 92 $scale), (Scale-Value 58 $scale))
  $graphics.DrawEllipse($thinOutline, (Scale-Value 82 $scale), (Scale-Value 122 $scale), (Scale-Value 92 $scale), (Scale-Value 58 $scale))

  $graphics.FillEllipse($dark, (Scale-Value 82 $scale), (Scale-Value 104 $scale), (Scale-Value 18 $scale), (Scale-Value 22 $scale))
  $graphics.FillEllipse($dark, (Scale-Value 156 $scale), (Scale-Value 104 $scale), (Scale-Value 18 $scale), (Scale-Value 22 $scale))
  $graphics.FillEllipse($dark, (Scale-Value 119 $scale), (Scale-Value 130 $scale), (Scale-Value 18 $scale), (Scale-Value 14 $scale))

  $graphics.DrawLine($thinOutline, (Scale-Value 108 $scale), (Scale-Value 158 $scale), (Scale-Value 126 $scale), (Scale-Value 166 $scale))
  $graphics.DrawLine($thinOutline, (Scale-Value 126 $scale), (Scale-Value 166 $scale), (Scale-Value 148 $scale), (Scale-Value 158 $scale))

  $graphics.FillRectangle($green, (Scale-Value 66 $scale), (Scale-Value 184 $scale), (Scale-Value 124 $scale), (Scale-Value 32 $scale))
  $graphics.DrawRectangle($thinOutline, (Scale-Value 66 $scale), (Scale-Value 184 $scale), (Scale-Value 124 $scale), (Scale-Value 32 $scale))

  $fontSize = [Math]::Max(8, (Scale-Value 23 $scale))
  $font = New-Object System.Drawing.Font('Consolas', $fontSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
  $graphics.DrawString('>_', $font, $dark, (Scale-Value 84 $scale), (Scale-Value 188 $scale))

  $graphics.FillEllipse($amber, (Scale-Value 184 $scale), (Scale-Value 78 $scale), (Scale-Value 18 $scale), (Scale-Value 18 $scale))

  $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $font.Dispose()
  $outline.Dispose()
  $thinOutline.Dispose()
  $face.Dispose()
  $muzzle.Dispose()
  $ear.Dispose()
  $green.Dispose()
  $amber.Dispose()
  $dark.Dispose()
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
  $dimension = if ($size -eq 256) { 0 } else { $size }
  $writer.Write([byte]$dimension)
  $writer.Write([byte]$dimension)
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
