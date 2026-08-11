import type { ProjectExtensions, ProjectSessionSummary } from "../../electron/ipcTypes";
import type { I18nKey } from "../i18n";
import { ExtensionsPanel } from "./ExtensionsPanel";
import { GitStatusBadge } from "./GitStatusBadge";
import { ProjectHistoryPanel } from "./ProjectHistoryPanel";

type ProjectSidebarProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
  projectSessions: ProjectSessionSummary[];
  activeSessionId: string;
  sessionError: string;
  extensions: ProjectExtensions;
  extensionError: string;
  onRefreshSessions: () => void;
  onResumeSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  onRefreshExtensions: () => void;
  onInstallSkill: () => void;
  onInstallPlugin: () => void;
};

export function ProjectSidebar({
  t,
  projectPath,
  projectSessions,
  activeSessionId,
  sessionError,
  extensions,
  extensionError,
  onRefreshSessions,
  onResumeSession,
  onCreateSession,
  onDeleteSession,
  onRefreshExtensions,
  onInstallSkill,
  onInstallPlugin,
}: ProjectSidebarProps) {
  const projectName = projectPath.split(/[\\/]+/).filter(Boolean).at(-1) ?? projectPath;
  return (
    <aside className="project-sidebar workspace-column" aria-label={t("projectContext")}>
      <section className="sidebar-project-card">
        <div className="sidebar-project-heading">
          <div className="sidebar-project-mark">C</div>
          <div>
            <div className="sidebar-project-label">{t("project")}</div>
            <div className="sidebar-project-name">{projectName}</div>
          </div>
        </div>
        <div className="sidebar-project-path">{projectPath}</div>
        <GitStatusBadge t={t} projectPath={projectPath} />
      </section>
      <ProjectHistoryPanel
        t={t}
        sessions={projectSessions}
        activeSessionId={activeSessionId}
        error={sessionError}
        onRefresh={onRefreshSessions}
        onResume={onResumeSession}
        onCreate={onCreateSession}
        onDelete={onDeleteSession}
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
