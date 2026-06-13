import { describe, expect, it } from "vitest";
import { upsertRecentProject } from "../src/state/sessionIndex";

describe("upsertRecentProject", () => {
  it("adds a new project and keeps newest first", () => {
    const items = upsertRecentProject([], {
      path: "D:/repo",
      name: "repo",
      lastSessionId: "s1",
      lastUsedAt: "2026-06-11T00:00:00Z",
    });

    expect(items).toHaveLength(1);
    expect(items[0].path).toBe("D:/repo");
  });

  it("updates an existing project instead of duplicating it", () => {
    const items = upsertRecentProject(
      [
        {
          path: "D:/repo",
          name: "repo",
          lastSessionId: "old",
          lastUsedAt: "2026-06-10T00:00:00Z",
        },
      ],
      {
        path: "D:/repo",
        name: "repo",
        lastSessionId: "new",
        lastUsedAt: "2026-06-11T00:00:00Z",
      },
    );

    expect(items).toHaveLength(1);
    expect(items[0].lastSessionId).toBe("new");
  });
});
