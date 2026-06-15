import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import type { ProjectSessionDetail, ProjectSessionMessage, ProjectSessionSummary } from "./ipcTypes.js";

type RawSession = {
  id?: unknown;
  created_at?: unknown;
  createdAt?: unknown;
  history?: unknown;
};

type RawHistoryMessage = {
  role?: unknown;
  content?: unknown;
  timestamp?: unknown;
  created_at?: unknown;
  createdAt?: unknown;
};

const PREVIEW_LIMIT = 120;

export async function listProjectSessions(projectPath: string): Promise<ProjectSessionSummary[]> {
  const sessionsDir = sessionsRoot(projectPath);
  let entries;
  try {
    entries = await readdir(sessionsDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const summaries = await Promise.all(
    entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .map(async (entry) => summarizeSessionFile(sessionsDir, entry.name)),
  );

  return summaries
    .filter((summary): summary is ProjectSessionSummary => summary !== null)
    .sort((left, right) => Date.parse(right.updatedAt || "") - Date.parse(left.updatedAt || ""));
}

export async function loadProjectSession(projectPath: string, sessionId: string): Promise<ProjectSessionDetail> {
  assertSafeSessionId(sessionId);
  const filePath = join(sessionsRoot(projectPath), `${sessionId}.json`);
  const session = await readSessionJson(filePath);
  const id = stringValue(session.id) || sessionId;
  return {
    id,
    messages: extractVisibleMessages(session),
  };
}

function sessionsRoot(projectPath: string): string {
  return join(projectPath, ".codecub", "sessions");
}

async function summarizeSessionFile(sessionsDir: string, fileName: string): Promise<ProjectSessionSummary | null> {
  const filePath = join(sessionsDir, fileName);
  try {
    const [session, fileStat] = await Promise.all([readSessionJson(filePath), stat(filePath)]);
    const id = stringValue(session.id) || fileName.replace(/\.json$/i, "");
    const messages = extractVisibleMessages(session);
    const latestMessage = [...messages].reverse().find((message) => message.content.trim());
    return {
      id,
      createdAt: stringValue(session.created_at) || stringValue(session.createdAt) || fileStat.birthtime.toISOString(),
      updatedAt: fileStat.mtime.toISOString(),
      messageCount: messages.length,
      preview: clipPreview(latestMessage?.content ?? ""),
    };
  } catch {
    return null;
  }
}

async function readSessionJson(filePath: string): Promise<RawSession> {
  const parsed = JSON.parse(await readFile(filePath, "utf-8"));
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Session file is not an object.");
  }
  return parsed as RawSession;
}

function extractVisibleMessages(session: RawSession): ProjectSessionMessage[] {
  if (!Array.isArray(session.history)) {
    return [];
  }

  const fallbackCreatedAt = stringValue(session.created_at) || stringValue(session.createdAt);
  return session.history
    .map((item) => normalizeHistoryMessage(item as RawHistoryMessage, fallbackCreatedAt))
    .filter((message): message is ProjectSessionMessage => message !== null);
}

function normalizeHistoryMessage(item: RawHistoryMessage, fallbackCreatedAt: string): ProjectSessionMessage | null {
  const role = stringValue(item.role);
  if (role !== "user" && role !== "assistant") {
    return null;
  }
  const content = stringValue(item.content);
  if (!content.trim()) {
    return null;
  }
  return {
    role,
    content,
    createdAt: stringValue(item.timestamp) || stringValue(item.created_at) || stringValue(item.createdAt) || fallbackCreatedAt,
  };
}

function assertSafeSessionId(sessionId: string): void {
  if (!sessionId || sessionId.includes("/") || sessionId.includes("\\") || sessionId === "." || sessionId === "..") {
    throw new Error("Invalid session id.");
  }
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function clipPreview(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > PREVIEW_LIMIT ? `${compact.slice(0, PREVIEW_LIMIT - 1)}…` : compact;
}
