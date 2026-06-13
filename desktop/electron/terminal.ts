import { EventEmitter } from "node:events";
import type { IPty } from "node-pty";
import { spawn } from "node-pty";
import type { TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "./ipcTypes.js";

export type TerminalEvents = {
  data: [terminalId: string, data: string];
  exit: [terminalId: string, exitCode: number | null];
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
    const shell = chooseTerminalShell();
    const pty = spawn(shell.file, shell.args, {
      name: "xterm-256color",
      cols: Math.max(20, request.cols),
      rows: Math.max(5, request.rows),
      cwd: request.cwd,
      env: process.env,
    });
    this.sessions.set(request.terminalId, pty);
    pty.onData((data) => this.emit("data", request.terminalId, data));
    pty.onExit((event) => {
      this.sessions.delete(request.terminalId);
      this.emit("exit", request.terminalId, event.exitCode ?? null);
    });
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
