import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import type { RecentProject } from "./ipcTypes.js";

function recentProjectsPath(): string {
  return join(app.getPath("userData"), "recent-projects.json");
}

export async function loadRecentProjects(): Promise<RecentProject[]> {
  try {
    const raw = await readFile(recentProjectsPath(), "utf-8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as RecentProject[]) : [];
  } catch {
    return [];
  }
}

export async function rememberProject(projectPath: string, sessionId = ""): Promise<RecentProject[]> {
  const current = await loadRecentProjects();
  const project = {
    path: projectPath,
    name: basename(projectPath),
    lastSessionId: sessionId,
    lastUsedAt: new Date().toISOString(),
  };
  const updated = [project, ...current.filter((item) => item.path !== project.path)].slice(0, 20);
  await mkdir(app.getPath("userData"), { recursive: true });
  await writeFile(recentProjectsPath(), JSON.stringify(updated, null, 2), "utf-8");
  return updated;
}
