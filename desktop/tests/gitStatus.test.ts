import { describe, expect, it } from "vitest";
import { parseGitPorcelainStatus, summarizeGitStatus } from "../electron/gitStatus";

describe("git status helpers", () => {
  it("counts changed files from porcelain output", () => {
    const files = parseGitPorcelainStatus(" M README.md\n?? new.txt\nA  added.ts\n");

    expect(files).toEqual(["README.md", "new.txt", "added.ts"]);
  });

  it("summarizes clean and dirty states", () => {
    expect(summarizeGitStatus("main", [])).toEqual({
      branch: "main",
      dirty: false,
      changedCount: 0,
      ahead: 0,
      behind: 0,
      files: [],
    });
    expect(summarizeGitStatus("feature", ["README.md"]).dirty).toBe(true);
  });
});
