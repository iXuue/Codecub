import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { BackendCommand } from "./ipcTypes.js";
import type { BackendLaunchConfig } from "./backendLaunchConfig.js";

export type BackendProcessEvents = {
  event: [line: string];
  exit: [code: number | null];
  error: [message: string];
};

export class BackendProcess extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private stdoutBuffer = "";
  private repoRoot: string;

  constructor(repoRoot = process.cwd()) {
    super();
    this.repoRoot = repoRoot;
  }

  override on<K extends keyof BackendProcessEvents>(
    eventName: K,
    listener: (...args: BackendProcessEvents[K]) => void,
  ): this {
    return super.on(eventName, listener);
  }

  override emit<K extends keyof BackendProcessEvents>(eventName: K, ...args: BackendProcessEvents[K]): boolean {
    return super.emit(eventName, ...args);
  }

  start(launchConfig: BackendLaunchConfig): void {
    this.stop();
    const bundledBackend = join(process.resourcesPath, "backend", "codecub-agent.exe");
    const hasBundledBackend = existsSync(bundledBackend);
    const command = hasBundledBackend ? bundledBackend : process.env.CODECUB_BACKEND_COMMAND || "uv";
    const args = hasBundledBackend
      ? launchConfig.args
      : process.env.CODECUB_BACKEND_COMMAND
        ? ["-m", "codecub", ...launchConfig.args]
        : ["run", "python", "-m", "codecub", ...launchConfig.args];

    this.child = spawn(command, args, {
      cwd: this.repoRoot,
      env: launchConfig.env,
      shell: false,
    });

    this.child.stdout.on("data", (chunk: Buffer) => {
      this.handleStdout(chunk.toString("utf-8"));
    });

    this.child.stderr.on("data", (chunk: Buffer) => {
      this.emit("error", chunk.toString("utf-8"));
    });

    this.child.on("error", (error) => {
      this.emit("error", error.message);
    });

    this.child.on("exit", (code) => {
      this.emit("exit", code);
      this.child = null;
    });
  }

  send(command: BackendCommand): void {
    if (!this.child) {
      this.emit("error", "Backend is not running");
      return;
    }
    const payload =
      command.type === "send_message" && !command.run_id ? { ...command, run_id: `run_${randomUUID()}` } : command;
    this.child.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  stop(): void {
    if (!this.child) {
      return;
    }
    this.child.stdin.write(`${JSON.stringify({ type: "close" })}\n`);
    this.child.kill();
    this.child = null;
  }

  private handleStdout(text: string): void {
    this.stdoutBuffer += text;
    let newlineIndex = this.stdoutBuffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.stdoutBuffer.slice(0, newlineIndex).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      if (line) {
        this.emit("event", line);
      }
      newlineIndex = this.stdoutBuffer.indexOf("\n");
    }
  }
}
