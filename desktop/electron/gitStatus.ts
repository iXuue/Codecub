import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { GitStatus } from "./ipcTypes.js";

const execFileAsync = promisify(execFile);

export function parseGitPorcelainStatus(output: string): string[] {
  return output
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
    .filter(Boolean);
}

export function summarizeGitStatus(branch: string, files: string[]): GitStatus {
  return {
    branch: branch || "-",
    dirty: files.length > 0,
    changedCount: files.length,
    ahead: 0,
    behind: 0,
    files,
  };
}

export async function readGitStatus(cwd: string): Promise<GitStatus> {
  try {
    const [{ stdout: branchOut }, { stdout: statusOut }] = await Promise.all([
      execFileAsync("git", ["branch", "--show-current"], { cwd, timeout: 5000 }),
      execFileAsync("git", ["status", "--porcelain"], { cwd, timeout: 5000 }),
    ]);
    return summarizeGitStatus(branchOut.trim() || "-", parseGitPorcelainStatus(statusOut));
  } catch {
    return summarizeGitStatus("-", []);
  }
}
