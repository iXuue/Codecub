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
- Icon status: generated local CodeCub pet icon from `desktop/scripts/generate-codecub-icon.ps1`; packaged through `desktop/build/icon.ico`.
- Electron runtime source: local project cache at `desktop/.electron-cache/electron-v39.8.10/electron-v39.8.10-win32-x64.zip`.
- Push status: local only until the user explicitly approves remote push.
