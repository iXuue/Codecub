import { describe, expect, it } from "vitest";
import type { TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "../electron/ipcTypes";

describe("terminal IPC types", () => {
  it("describes terminal start, write, and resize requests", () => {
    const start: TerminalStartRequest = { terminalId: "term-1", cwd: "D:/repo", cols: 100, rows: 30 };
    const write: TerminalWriteRequest = { terminalId: "term-1", data: "git status\r" };
    const resize: TerminalResizeRequest = { terminalId: "term-1", cols: 120, rows: 40 };

    expect(start.cwd).toBe("D:/repo");
    expect(write.data).toContain("git status");
    expect(resize.cols).toBe(120);
  });
});
