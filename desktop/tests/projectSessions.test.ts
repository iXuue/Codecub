import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { listProjectSessions, loadProjectSession } from "../electron/projectSessions";

async function writeSession(projectPath: string, fileName: string, data: unknown) {
  const sessionsDir = join(projectPath, ".codecub", "sessions");
  await mkdir(sessionsDir, { recursive: true });
  await writeFile(join(sessionsDir, fileName), JSON.stringify(data), "utf-8");
}

describe("projectSessions", () => {
  it("lists project sessions with compact summaries", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    await writeSession(projectPath, "s1.json", {
      id: "s1",
      created_at: "2026-06-15T00:00:00Z",
      history: [
        { role: "user", content: "hello" },
        { role: "assistant", content: "world" },
      ],
    });

    const sessions = await listProjectSessions(projectPath);

    expect(sessions).toHaveLength(1);
    expect(sessions[0]).toMatchObject({
      id: "s1",
      createdAt: "2026-06-15T00:00:00Z",
      messageCount: 2,
      preview: "world",
    });
  });

  it("ignores malformed session files", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    const sessionsDir = join(projectPath, ".codecub", "sessions");
    await mkdir(sessionsDir, { recursive: true });
    await writeFile(join(sessionsDir, "bad.json"), "{", "utf-8");
    await writeSession(projectPath, "good.json", { id: "good", history: [{ role: "user", content: "ok" }] });

    const sessions = await listProjectSessions(projectPath);

    expect(sessions.map((session) => session.id)).toEqual(["good"]);
  });

  it("loads only user and assistant messages", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    await writeSession(projectPath, "s1.json", {
      id: "s1",
      history: [
        { role: "system", content: "hidden" },
        { role: "user", content: "visible user", created_at: "2026-06-15T00:00:01Z" },
        { role: "tool", content: "tool output" },
        { role: "assistant", content: "visible assistant" },
      ],
    });

    const detail = await loadProjectSession(projectPath, "s1");

    expect(detail.messages).toEqual([
      { role: "user", content: "visible user", createdAt: "2026-06-15T00:00:01Z" },
      { role: "assistant", content: "visible assistant", createdAt: "" },
    ]);
  });

  it("rejects path traversal session ids", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    await expect(loadProjectSession(projectPath, "../outside")).rejects.toThrow("Invalid session id");
  });
});
