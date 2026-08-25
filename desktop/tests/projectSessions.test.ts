import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createProjectSession, deleteProjectSession, listProjectSessions, loadProjectSession } from "../electron/projectSessions";

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
      title: "hello",
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

  it("hides empty sessions unless they were created by the desktop UI", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id, "empty-filter");
    await writeSession(projectPath, "empty-legacy.json", { id: "empty-legacy", history: [] });
    await writeSession(projectPath, "empty-manual.json", {
      id: "empty-manual",
      created_by: "codecub-desktop",
      history: [],
    });

    const sessions = await listProjectSessions(projectPath);

    expect(sessions.map((session) => session.id)).toEqual(["empty-manual"]);
    expect(sessions[0].title).toBe("__codecub_empty_chat__");
    expect(sessions[0].messageCount).toBe(0);
  });

  it("creates and deletes desktop-managed sessions", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id, "create-delete");

    const created = await createProjectSession(projectPath);
    const sessionFile = join(projectPath, ".codecub", "sessions", `${created.id}.json`);
    const rawSession = JSON.parse(await readFile(sessionFile, "utf-8"));

    expect(rawSession).toMatchObject({
      id: created.id,
      created_by: "codecub-desktop",
      history: [],
    });

    await deleteProjectSession(projectPath, created.id);
    const sessions = await listProjectSessions(projectPath);

    expect(sessions).toEqual([]);
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

  it("does not surface legacy mojibake in session summaries or messages", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    await writeSession(projectPath, "garbled.json", {
      id: "garbled",
      history: [{ role: "user", content: "浣犵湅涓�涓嬫垜鐨勪唬鐮佷粨搴�" }],
    });

    const sessions = await listProjectSessions(projectPath);
    const detail = await loadProjectSession(projectPath, "garbled");

    expect(sessions[0].title).toBe("历史消息编码异常，无法完整恢复");
    expect(sessions[0].preview).toBe("历史消息编码异常，无法完整恢复");
    expect(detail.messages[0].content).toBe("历史消息编码异常，无法完整恢复");
  });

  it("rejects path traversal session ids", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id);
    await expect(loadProjectSession(projectPath, "../outside")).rejects.toThrow("Invalid session id");
  });
});
