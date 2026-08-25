$ErrorActionPreference = 'Stop'

$desktopRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$buildDir = Join-Path $desktopRoot 'build'
$sourcePath = Join-Path $buildDir 'icon-source.webp'

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

if (-not (Test-Path -LiteralPath $sourcePath)) {
  throw "Icon source spritesheet not found: $sourcePath"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  throw 'Python is required to generate icons from the WebP spritesheet.'
}

$env:CODECUB_ICON_SOURCE = $sourcePath
$env:CODECUB_ICON_OUTPUT = $buildDir

$pythonCode = @'
import os
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Pillow is required to generate icons from the WebP spritesheet.") from exc

source = Path(os.environ["CODECUB_ICON_SOURCE"])
out_dir = Path(os.environ["CODECUB_ICON_OUTPUT"])
out_dir.mkdir(parents=True, exist_ok=True)

sheet = Image.open(source).convert("RGBA")

# The pet spritesheet is arranged as 8 columns by 9 rows. The selected
# application icon is the first sprite in the top-left cell.
cell_w = sheet.width // 8
cell_h = sheet.height // 9
sprite = sheet.crop((0, 0, cell_w, cell_h))

def is_background(pixel):
    r, g, b, a = pixel
    if a == 0:
        return True

    is_black_fill = r <= 18 and g <= 18 and b <= 28
    is_blue_fill = b >= 55 and b >= r + 35 and b >= g + 20
    is_dark_blue_edge = b >= 25 and b >= r + 18 and b >= g + 12 and r <= 45 and g <= 45
    is_green_artifact = g >= 70 and r <= 70 and b <= 80

    return is_black_fill or is_blue_fill or is_dark_blue_edge or is_green_artifact

def remove_connected_background(image):
    pixels = image.load()
    width, height = image.size
    visited = set()
    stack = []

    for x in range(width):
        stack.append((x, 0))
        stack.append((x, height - 1))
    for y in range(height):
        stack.append((0, y))
        stack.append((width - 1, y))

    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= width or y >= height or (x, y) in visited:
            continue
        visited.add((x, y))
        if not is_background(pixels[x, y]):
            continue

        pixels[x, y] = (0, 0, 0, 0)
        stack.append((x + 1, y))
        stack.append((x - 1, y))
        stack.append((x, y + 1))
        stack.append((x, y - 1))

    return image

def remove_blue_remnants(image):
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            is_blue_remnant = b >= 50 and b >= r + 28 and b >= g + 15
            is_dark_blue_remnant = b >= 24 and b >= r + 14 and b >= g + 8 and r <= 54 and g <= 54
            if is_blue_remnant or is_dark_blue_remnant:
                pixels[x, y] = (0, 0, 0, 0)
    return image

sprite = remove_connected_background(sprite)
sprite = remove_blue_remnants(sprite)

# Remove isolated transparent-border noise, then crop to the cleaned character.
bbox = sprite.getchannel("A").getbbox()
if not bbox:
    raise SystemExit("Selected sprite cell does not contain visible pixels after background cleanup.")

sprite = sprite.crop(bbox)

sizes = [256, 128, 64, 48, 32, 16]
rendered = []

for size in sizes:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = max(1, round(size * 0.06))
    max_w = size - pad * 2
    max_h = size - pad * 2
    scale = min(max_w / sprite.width, max_h / sprite.height)
    resized = sprite.resize(
        (
            max(1, round(sprite.width * scale)),
            max(1, round(sprite.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    resized = remove_blue_remnants(resized)
    x = (size - resized.width) // 2
    y = (size - resized.height) // 2
    canvas.alpha_composite(resized, (x, y))
    canvas.save(out_dir / f"icon-{size}.png")
    rendered.append((size, canvas.copy()))

preview = rendered[0][1]
preview.save(out_dir / "icon-preview.png")
preview.save(out_dir / "icon.ico", format="ICO", sizes=[(size, size) for size, _ in rendered])

print(f"Generated CodeCub icon from {source}")
print(f"Sheet: {sheet.width}x{sheet.height}; cell: {cell_w}x{cell_h}; sprite: {sprite.width}x{sprite.height}")
'@

$pythonCode | & $python.Source -
if ($LASTEXITCODE -ne 0) {
  throw "Python icon generation failed with exit code $LASTEXITCODE"
}

Get-Item (Join-Path $buildDir 'icon.ico'), (Join-Path $buildDir 'icon-preview.png') |
  Select-Object FullName, Length
