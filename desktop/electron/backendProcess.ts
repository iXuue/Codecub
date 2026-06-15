import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import type { BackendCommand } from "./ipcTypes.js";
import type { BackendLaunchConfig } from "./backendLaunchConfig.js";

export type BackendProcessEvents = {
  event: [line: string];
  exit: [code: number | null];
  error: [message: string];
};

export type BackendCommandResolution = {
  command: string;
  args: string[];
};

export function resolveBackendCommand(
  launchConfig: BackendLaunchConfig,
  options: {
    resourcesPath?: string;
    packaged?: boolean;
    env?: NodeJS.ProcessEnv;
    exists?: (path: string) => boolean;
  } = {},
): BackendCommandResolution {
  const resourcesPath = options.resourcesPath ?? process.resourcesPath;
  const packaged = options.packaged ?? process.defaultApp !== true;
  const env = options.env ?? process.env;
  const exists = options.exists ?? existsSync;
  const bundledBackend = join(resourcesPath, "backend", "codecub-agent.exe");

  if (packaged) {
    if (!exists(bundledBackend)) {
      throw new Error(
        [
          "Bundled CodeCub backend executable is missing.",
          `Expected: ${bundledBackend}`,
          `resourcesPath: ${resourcesPath}`,
          "Rebuild the Windows package and verify release/win-unpacked/resources/backend/codecub-agent.exe exists.",
        ].join("\n"),
      );
    }
    return { command: bundledBackend, args: launchConfig.args };
  }

  if (env.CODECUB_BACKEND_COMMAND) {
    return { command: env.CODECUB_BACKEND_COMMAND, args: ["-m", "codecub", ...launchConfig.args] };
  }

  return { command: "uv", args: ["run", "python", "-m", "codecub", ...launchConfig.args] };
}

export function resolveBackendCwd(repoRoot: string, fallback = process.resourcesPath): string {
  if (repoRoot.includes(".asar")) {
    return fallback;
  }

  try {
    if (statSync(repoRoot).isDirectory()) {
      return repoRoot;
    }
  } catch {
    // Fall through to the packaged resources directory.
  }
  return fallback;
}

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
    const { command, args } = resolveBackendCommand(launchConfig);
    const cwd = resolveBackendCwd(this.repoRoot);

    this.child = spawn(command, args, {
      cwd,
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
