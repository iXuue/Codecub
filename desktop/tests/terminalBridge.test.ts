import { describe, expect, it } from "vitest";
import { chooseTerminalShell } from "../electron/terminal";

describe("chooseTerminalShell", () => {
  it("uses PowerShell on Windows by default", () => {
    const shell = chooseTerminalShell("win32", { SystemRoot: "C:\\Windows" });

    expect(shell.file).toContain("powershell.exe");
    expect(shell.args).toContain("-NoLogo");
  });

  it("falls back to sh on non-Windows", () => {
    const shell = chooseTerminalShell("linux", {});

    expect(shell.file).toBe("sh");
  });
});
