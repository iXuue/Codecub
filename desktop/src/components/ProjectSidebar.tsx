import type { ProjectExtensions, ProjectSessionSummary } from "../../electron/ipcTypes";
import type { I18nKey } from "../i18n";
import { ExtensionsPanel } from "./ExtensionsPanel";
import { GitStatusBadge } from "./GitStatusBadge";
import { ProjectHistoryPanel } from "./ProjectHistoryPanel";

type ProjectSidebarProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
  projectSessions: ProjectSessionSummary[];
  sessionError: string;
  extensions: ProjectExtensions;
  extensionError: string;
  onRefreshSessions: () => void;
  onResumeSession: (sessionId: string) => void;
  onRefreshExtensions: () => void;
  onInstallSkill: () => void;
  onInstallPlugin: () => void;
};

export function ProjectSidebar({
  t,
  projectPath,
  projectSessions,
  sessionError,
  extensions,
  extensionError,
  onRefreshSessions,
  onResumeSession,
  onRefreshExtensions,
  onInstallSkill,
  onInstallPlugin,
}: ProjectSidebarProps) {
  return (
    <aside className="project-sidebar" aria-label={t("projectContext")}>
      <section className="sidebar-project-card">
        <div className="sidebar-project-label">{t("project")}</div>
        <div className="sidebar-project-path">{projectPath}</div>
        <GitStatusBadge t={t} projectPath={projectPath} />
      </section>
      <ProjectHistoryPanel
        t={t}
        sessions={projectSessions}
        error={sessionError}
        onRefresh={onRefreshSessions}
        onResume={onResumeSession}
      />
      <ExtensionsPanel
        t={t}
        extensions={extensions}
        error={extensionError}
        onRefresh={onRefreshExtensions}
        onInstallSkill={onInstallSkill}
        onInstallPlugin={onInstallPlugin}
      />
    </aside>
  );
}
