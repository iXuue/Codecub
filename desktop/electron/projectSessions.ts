import { randomUUID } from "node:crypto";
import { mkdir, readdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type {
  CreateProjectSessionResult,
  DeleteProjectSessionResult,
  ProjectSessionDetail,
  ProjectSessionMessage,
  ProjectSessionSummary,
} from "./ipcTypes.js";

type RawSession = {
  id?: unknown;
  title?: unknown;
  created_by?: unknown;
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

const DESKTOP_CREATED_BY = "codecub-desktop";
const EMPTY_CHAT_TITLE = "__codecub_empty_chat__";
const ENCODING_ERROR_TEXT = "历史消息编码异常，无法完整恢复";
const PREVIEW_LIMIT = 120;
const TITLE_LIMIT = 54;
const MOJIBAKE_PATTERNS = [
  /\uFFFD/,
  /锟/,
  /浣犵|浣犲/,
  /鏌ョ|鎴戠|鐨勪|浠ｇ|鐮佷|涓嬫|骞蹭|粈涔|涔堢|堢殑/,
];

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

export async function createProjectSession(projectPath: string): Promise<CreateProjectSessionResult> {
  const sessionsDir = sessionsRoot(projectPath);
  await mkdir(sessionsDir, { recursive: true });
  const createdAt = new Date().toISOString();
  const id = `${compactTimestamp(createdAt)}-${randomUUID().slice(0, 6)}`;
  await writeFile(
    join(sessionsDir, `${id}.json`),
    JSON.stringify(
      {
        id,
        title: EMPTY_CHAT_TITLE,
        created_by: DESKTOP_CREATED_BY,
        created_at: createdAt,
        workspace_root: projectPath,
        history: [],
      },
      null,
      2,
    ),
    "utf-8",
  );
  return { id, createdAt };
}

export async function deleteProjectSession(projectPath: string, sessionId: string): Promise<DeleteProjectSessionResult> {
  assertSafeSessionId(sessionId);
  await unlink(join(sessionsRoot(projectPath), `${sessionId}.json`));
  return { deleted: true };
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
    const userCreated = stringValue(session.created_by) === DESKTOP_CREATED_BY;
    if (messages.length === 0 && !userCreated) {
      return null;
    }

    const firstUserMessage = messages.find((message) => message.role === "user" && message.content.trim());
    const latestMessage = [...messages].reverse().find((message) => message.content.trim());
    return {
      id,
      title: clipTitle(readableSessionText(stringValue(session.title) || firstUserMessage?.content || (userCreated ? EMPTY_CHAT_TITLE : ""))),
      createdAt: stringValue(session.created_at) || stringValue(session.createdAt) || fileStat.birthtime.toISOString(),
      updatedAt: fileStat.mtime.toISOString(),
      messageCount: messages.length,
      preview: clipPreview(readableSessionText(latestMessage?.content ?? "")),
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
  const content = readableSessionText(stringValue(item.content));
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
  return compact.length > PREVIEW_LIMIT ? `${compact.slice(0, PREVIEW_LIMIT - 3)}...` : compact;
}

function clipTitle(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > TITLE_LIMIT ? `${compact.slice(0, TITLE_LIMIT - 3)}...` : compact;
}

function readableSessionText(value: string): string {
  if (!value) {
    return "";
  }
  if (isLikelyGarbledText(value)) {
    return ENCODING_ERROR_TEXT;
  }
  return value;
}

function isLikelyGarbledText(value: string): boolean {
  if (hasBrokenSurrogate(value)) {
    return true;
  }
  return MOJIBAKE_PATTERNS.some((pattern) => pattern.test(value));
}

function hasBrokenSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        index += 1;
        continue;
      }
      return true;
    }
    if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function compactTimestamp(value: string): string {
  return value.replace(/\D/g, "").slice(0, 14);
}
