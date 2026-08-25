import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import type { BackendCommand } from "./ipcTypes.js";
import type { BackendLaunchConfig } from "./backendLaunchConfig.js";

export type BackendProcessEvents = {
  event: [line: string];
  exit: [event: BackendExitEvent];
  error: [message: string];
};

export type BackendExitEvent = {
  code: number | null;
  signal: NodeJS.Signals | null;
  expected: boolean;
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

export function backendExitErrorMessage(event: BackendExitEvent): string | null {
  if (event.expected) {
    return null;
  }

  if (event.code !== null) {
    return `Backend exited with code ${event.code}`;
  }

  if (event.signal) {
    return `Backend exited after signal ${event.signal}`;
  }

  return "Backend exited unexpectedly";
}

export function encodeAsciiJsonLine(value: unknown): string {
  const json = JSON.stringify(value);
  if (!json) {
    throw new Error("Backend command must be JSON serializable.");
  }
  return `${json.replace(/[^\x00-\x7F]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`)}\n`;
}

export class BackendProcess extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private expectedExitChildren = new WeakSet<ChildProcessWithoutNullStreams>();
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

    const child = spawn(command, args, {
      cwd,
      env: launchConfig.env,
      shell: false,
    });
    this.child = child;

    child.stdout.on("data", (chunk: Buffer) => {
      this.handleStdout(chunk.toString("utf-8"));
    });

    child.stderr.on("data", (chunk: Buffer) => {
      this.emit("error", chunk.toString("utf-8"));
    });

    child.on("error", (error) => {
      this.emit("error", error.message);
    });

    child.on("exit", (code, signal) => {
      const expected = this.expectedExitChildren.has(child);
      this.emit("exit", { code, signal, expected });
      if (this.child === child) {
        this.child = null;
      }
    });
  }

  send(command: BackendCommand): void {
    if (!this.child) {
      this.emit("error", "Backend is not running");
      return;
    }
    this.child.stdin.write(encodeAsciiJsonLine(command), "utf-8");
  }

  stop(): void {
    if (!this.child) {
      return;
    }
    const child = this.child;
    this.expectedExitChildren.add(child);
    try {
      child.stdin.write(encodeAsciiJsonLine({ type: "close" }), "utf-8");
    } catch {
      // The process may already be closing; the expected exit marker still suppresses a false error.
    }
    child.kill();
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
