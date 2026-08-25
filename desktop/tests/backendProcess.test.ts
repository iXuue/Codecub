import { describe, expect, it, vi } from "vitest";
import {
  BackendProcess,
  backendExitErrorMessage,
  encodeAsciiJsonLine,
  resolveBackendCommand,
  resolveBackendCwd,
} from "../electron/backendProcess";
import type { BackendLaunchConfig } from "../electron/backendLaunchConfig";

const launchConfig: BackendLaunchConfig = {
  args: ["--app-mode", "--cwd", "D:/repo"],
  env: {},
};

describe("resolveBackendCommand", () => {
  it("uses bundled backend in packaged mode when it exists", () => {
    const resolved = resolveBackendCommand(launchConfig, {
      packaged: true,
      resourcesPath: "D:/app/resources",
      exists: () => true,
      env: {},
    });

    expect(resolved.command).toMatch(/[\\/]backend[\\/]codecub-agent\.exe$/);
    expect(resolved.args).toEqual(launchConfig.args);
  });

  it("throws a clear error when packaged backend is missing", () => {
    expect(() =>
      resolveBackendCommand(launchConfig, {
        packaged: true,
        resourcesPath: "D:/app/resources",
        exists: () => false,
        env: {},
      }),
    ).toThrow(/Bundled CodeCub backend executable is missing[\s\S]*codecub-agent\.exe/);
  });

  it("keeps the uv fallback for development mode", () => {
    const resolved = resolveBackendCommand(launchConfig, {
      packaged: false,
      resourcesPath: "D:/app/resources",
      exists: () => false,
      env: {},
    });

    expect(resolved.command).toBe("uv");
    expect(resolved.args).toEqual(["run", "python", "-m", "codecub", ...launchConfig.args]);
  });

  it("falls back to resourcesPath when repo root is not a directory", () => {
    const cwd = resolveBackendCwd("Z:/definitely-missing-codecub-app.asar", "D:/app/resources");

    expect(cwd).toBe("D:/app/resources");
  });

  it("does not use an asar virtual path as a native process cwd", () => {
    const cwd = resolveBackendCwd("D:/app/resources/app.asar", "D:/app/resources");

    expect(cwd).toBe("D:/app/resources");
  });
});

describe("backendExitErrorMessage", () => {
  it("suppresses expected backend exits", () => {
    expect(backendExitErrorMessage({ code: null, signal: "SIGTERM", expected: true })).toBeNull();
  });

  it("reports unexpected backend exit codes clearly", () => {
    expect(backendExitErrorMessage({ code: 1, signal: null, expected: false })).toBe("Backend exited with code 1");
  });

  it("does not report unexpected null exits as unknown", () => {
    expect(backendExitErrorMessage({ code: null, signal: null, expected: false })).toBe(
      "Backend exited unexpectedly",
    );
  });
});

describe("encodeAsciiJsonLine", () => {
  it("escapes Unicode command payloads for stdin transport", () => {
    const line = encodeAsciiJsonLine({ type: "send_message", message: "查看我的代码" });

    expect(line).toContain("\\u67e5\\u770b");
    expect(line).not.toContain("查看我的代码");
    expect(Buffer.from(line, "utf-8").toString("ascii")).toBe(line);
    expect(JSON.parse(line).message).toBe("查看我的代码");
  });
});

describe("BackendProcess.send", () => {
  it("does not synthesize random run ids before forwarding messages", () => {
    const write = vi.fn();
    const backend = new BackendProcess("D:/repo");
    (backend as unknown as { child: { stdin: { write: typeof write } } }).child = { stdin: { write } };

    backend.send({ type: "send_message", message: "inspect repo" });

    const line = write.mock.calls[0][0] as string;
    expect(JSON.parse(line)).toEqual({ type: "send_message", message: "inspect repo" });
    expect(write.mock.calls[0][1]).toBe("utf-8");
  });
});
