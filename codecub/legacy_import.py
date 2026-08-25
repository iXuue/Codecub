import json
import shutil
from pathlib import Path


def detect_legacy_pico(project_root):
    root = Path(project_root)
    session_dir = root / ".pico" / "sessions"
    files = sorted(session_dir.glob("*.json")) if session_dir.is_dir() else []
    return {
        "exists": session_dir.is_dir(),
        "session_count": len(files),
        "session_paths": [str(path) for path in files],
    }


def import_legacy_pico_sessions(project_root):
    root = Path(project_root)
    source_dir = root / ".pico" / "sessions"
    target_dir = root / ".codecub" / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    imported = 0
    skipped = 0
    errors = []

    sources = sorted(source_dir.glob("*.json")) if source_dir.is_dir() else []
    for source in sources:
        target = target_dir / source.name
        if target.exists():
            skipped += 1
            continue
        try:
            json.loads(source.read_text(encoding="utf-8"))
            shutil.copy2(source, target)
            imported += 1
        except Exception as exc:
            errors.append({"path": str(source), "message": str(exc)})

    return {
        "imported_count": imported,
        "skipped_count": skipped,
        "errors": errors,
    }
