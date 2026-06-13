export type RecentProject = {
  path: string;
  name: string;
  lastSessionId: string;
  lastUsedAt: string;
};

export type SessionIndexItem = {
  projectPath: string;
  sessionId: string;
  title: string;
  createdAt: string;
  lastUsedAt: string;
  provider: string;
  model: string;
  lastMessage: string;
};

export function upsertRecentProject(items: RecentProject[], project: RecentProject): RecentProject[] {
  return [project, ...items.filter((item) => item.path !== project.path)].slice(0, 20);
}
