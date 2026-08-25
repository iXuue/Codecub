import { EventEmitter } from "node:events";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import type { IPty } from "node-pty";
import { spawn } from "node-pty";
import type { TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "./ipcTypes.js";

export type TerminalEvents = {
  data: [terminalId: string, data: string];
  exit: [terminalId: string, exitCode: number | null];
  error: [terminalId: string, message: string];
};

export function chooseTerminalShell(
  platform = process.platform,
  env: Record<string, string | undefined> = process.env,
): { file: string; args: string[] } {
  if (platform === "win32") {
    const systemRoot = env.SystemRoot || "C:\\Windows";
    return {
      file: `${systemRoot}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe`,
      args: ["-NoLogo"],
    };
  }
  return { file: env.SHELL || "sh", args: [] };
}

export type TerminalCodecubCommand = {
  command: string;
  args: string[];
};

export function resolveTerminalCodecubCommand(
  options: {
    resourcesPath?: string;
    packaged?: boolean;
    env?: NodeJS.ProcessEnv;
    exists?: (path: string) => boolean;
  } = {},
): TerminalCodecubCommand {
  const resourcesPath = options.resourcesPath ?? process.resourcesPath;
  const packaged = options.packaged ?? process.defaultApp !== true;
  const env = options.env ?? process.env;
  const exists = options.exists ?? existsSync;
  const bundledBackend = join(resourcesPath, "backend", "codecub-agent.exe");

  if (packaged && exists(bundledBackend)) {
    return { command: bundledBackend, args: [] };
  }
  if (env.CODECUB_BACKEND_COMMAND) {
    return { command: env.CODECUB_BACKEND_COMMAND, args: ["-m", "codecub"] };
  }
  return { command: "uv", args: ["run", "python", "-m", "codecub"] };
}

export function buildPowerShellCodecubBootstrap(codecub: TerminalCodecubCommand): string {
  const command = psQuote(codecub.command);
  const args = codecub.args.map(psQuote).join(", ");
  const seedArgs = args ? `@(${args})` : "@()";
  return [
    `function global:codecub { $codecubCwd = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Location).Path)); $codecubArgs = ${seedArgs} + @('--cwd-b64', $codecubCwd) + $args; & ${command} @codecubArgs }`,
    "Write-Host 'CodeCub terminal ready. Type codecub to start the agent.' -ForegroundColor Cyan",
  ].join("; ");
}

function psQuote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

export class TerminalManager extends EventEmitter {
  private sessions = new Map<string, IPty>();

  override on<K extends keyof TerminalEvents>(
    eventName: K,
    listener: (...args: TerminalEvents[K]) => void,
  ): this {
    return super.on(eventName, listener);
  }

  override emit<K extends keyof TerminalEvents>(eventName: K, ...args: TerminalEvents[K]): boolean {
    return super.emit(eventName, ...args);
  }

  start(request: TerminalStartRequest): void {
    this.close(request.terminalId);
    try {
      if (!existsSync(request.cwd)) {
        throw new Error(`Terminal cwd does not exist: ${request.cwd}`);
      }
      const shell = chooseTerminalShell();
      const codecub = resolveTerminalCodecubCommand();
      const shellArgs =
        process.platform === "win32" ? [...shell.args, "-NoExit", "-Command", buildPowerShellCodecubBootstrap(codecub)] : shell.args;
      const terminalEnv = { ...process.env };
      terminalEnv.PYTHONUTF8 = "1";
      terminalEnv.PYTHONIOENCODING = "utf-8";
      if (process.platform === "win32" && codecub.command.includes("\\")) {
        terminalEnv.PATH = `${dirname(codecub.command)};${terminalEnv.PATH ?? ""}`;
      }
      const pty = spawn(shell.file, shellArgs, {
        name: "xterm-256color",
        cols: Math.max(20, request.cols),
        rows: Math.max(5, request.rows),
        cwd: request.cwd,
        env: terminalEnv,
      });
      this.sessions.set(request.terminalId, pty);
      pty.onData((data) => this.emit("data", request.terminalId, data));
      pty.onExit((event) => {
        this.sessions.delete(request.terminalId);
        this.emit("exit", request.terminalId, event.exitCode ?? null);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.emit("error", request.terminalId, message);
    }
  }

  write(request: TerminalWriteRequest): void {
    this.sessions.get(request.terminalId)?.write(request.data);
  }

  resize(request: TerminalResizeRequest): void {
    this.sessions.get(request.terminalId)?.resize(Math.max(20, request.cols), Math.max(5, request.rows));
  }

  close(terminalId: string): void {
    const session = this.sessions.get(terminalId);
    if (!session) {
      return;
    }
    session.kill();
    this.sessions.delete(terminalId);
  }

  closeAll(): void {
    for (const terminalId of this.sessions.keys()) {
      this.close(terminalId);
    }
  }
}
