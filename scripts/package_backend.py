from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    dist_dir = repo / "desktop" / "resources" / "backend"
    build_dir = dist_dir / "build" / datetime.now().strftime("%Y%m%d-%H%M%S")
    spec_dir = dist_dir / "spec"
    entrypoint = dist_dir / "codecub_backend_entry.py"

    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text(
        "from codecub.cli import main\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "codecub-agent",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        str(entrypoint),
    ]
    subprocess.run(command, cwd=repo, check=True)

    executable = dist_dir / ("codecub-agent.exe" if sys.platform == "win32" else "codecub-agent")
    if not executable.exists():
        raise SystemExit(f"missing backend executable: {executable}")
    print(executable)


if __name__ == "__main__":
    main()
