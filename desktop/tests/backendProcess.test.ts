import { describe, expect, it } from "vitest";
import { resolveBackendCommand, resolveBackendCwd } from "../electron/backendProcess";
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
