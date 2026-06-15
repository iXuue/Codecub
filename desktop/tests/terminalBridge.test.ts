import { describe, expect, it } from "vitest";
import { chooseTerminalShell, TerminalManager } from "../electron/terminal";

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

  it("emits a terminal error when cwd does not exist", async () => {
    const manager = new TerminalManager();
    const event = await new Promise<{ terminalId: string; message: string }>((resolve) => {
      manager.on("error", (terminalId, message) => resolve({ terminalId, message }));
      manager.start({ terminalId: "term-missing", cwd: "Z:/definitely-missing-codecub-path", cols: 100, rows: 24 });
    });

    expect(event.terminalId).toBe("term-missing");
    expect(event.message).toContain("Terminal cwd does not exist");
  });
});
