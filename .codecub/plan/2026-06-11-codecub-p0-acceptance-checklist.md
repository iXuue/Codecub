# CodeCub P0 验收清单

## 后端

- `uv run pytest -q -ra --durations=10` 通过。
- `uv run python -m codecub --help` 退出码为 0。
- `desktop/resources/backend/codecub-agent.exe --help` 退出码为 0。
- App-mode 能输出 `session_started`、`user_message_received`、`assistant_message`、`run_completed` 或 `run_failed` 等 JSONL 事件。
- 终端输出只经过桌面端 terminal IPC，不进入 agent app-mode JSONL 流。
- 危险工具在 `ask` 策略下必须等待 approve/reject，不得直接执行。
- `.pico` legacy 数据只在收到 `import_legacy_pico` 命令后导入。

## 桌面端

- `cd desktop && npm test` 通过。
- `cd desktop && npm run typecheck` 通过。
- `cd desktop && npm run build` 通过。
- Electron dev smoke：`electron_alive_after_6s=True`。
- 可以打开项目并启动后端会话。
- 聊天消息能发送到后端。
- 运行日志显示后端事件。
- 危险操作出现审批弹窗。
- Diff preview 显示变更文件摘要。
- 终端可以打开，并以所选项目目录作为 cwd。
- Git badge 显示 branch、dirty state、changed file count，并支持刷新。

## Windows 打包

- `uv run python scripts/package_backend.py` 能生成 `desktop/resources/backend/codecub-agent.exe`。
- `cd desktop && npm run package:win` 通过。
- `desktop/release/win-unpacked/CodeCub.exe` 存在。
- `desktop/release/win-unpacked/resources/backend/codecub-agent.exe` 存在。
- packaged app smoke：`packaged_alive_after_8s=True`。

## 当前验收结果

- 2026-06-13 12:16 Asia/Shanghai：后端全量测试通过，`137 passed, 2 skipped, 6 warnings`。
- 2026-06-13 12:16 Asia/Shanghai：CLI help 通过。
- 2026-06-13 12:16 Asia/Shanghai：桌面 Vitest 通过，`7 passed` test files / `13 passed` tests。
- 2026-06-13 12:16 Asia/Shanghai：桌面 typecheck 通过。
- 2026-06-13 12:16 Asia/Shanghai：桌面 production build 通过；存在 Vite chunk size warning，未阻断 P0。
- 2026-06-13 12:08 Asia/Shanghai：Windows packaged app smoke 通过。

## 已知风险

- `npm audit` 当前报告 8 个漏洞：6 high、2 critical。未执行 `npm audit fix --force`，因为它可能引入破坏性升级，需要单独安全修复计划。
- `node-pty` 在本机缺少 Visual Studio C++ Build Tools 时不能 rebuild。当前 Windows 打包配置使用 `npmRebuild: false` 并 unpack `node-pty` 预编译二进制；已通过 packaged smoke。
- `@xterm/xterm` 进入前端主 bundle 后触发 Vite 542KB chunk warning。P0 可接受，P1 可考虑动态导入终端面板。
## P0.6 Release Hardening Status

- Dependency audit: completed with `npm audit --audit-level=high`; result: `found 0 vulnerabilities`.
- Windows installer: completed with `npm run package:win`; generated `desktop/release/CodeCub-0.1.0-x64.exe`.
- Packaged smoke: completed with `desktop/scripts/smoke-packaged.ps1`; result: `packaged_alive_after_8s=True`.
- Terminal bundle: lazy-loaded through dynamic import; initial renderer JS is about 211.67 kB and xterm is split into a separate chunk.
- Icon status: generated local CodeCub pet icon from `desktop/scripts/generate-codecub-icon.ps1`; packaged through `desktop/build/icon.ico`.
- Electron runtime source: local project cache at `desktop/.electron-cache/electron-v39.8.10/electron-v39.8.10-win32-x64.zip`.
- Remaining release risks are recorded in `.codecub/plan/2026-06-13-codecub-p0-6-release-hardening-plan.md`.
