import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ApprovalState } from "../state/approvalState";
import type { ChatState } from "../state/chatState";
import type { ProjectExtensions, ProjectSessionSummary } from "../../electron/ipcTypes";
import { ChatView } from "./ChatView";
import { LegacyImportPrompt } from "./LegacyImportPrompt";
import { ProjectSidebar } from "./ProjectSidebar";
import { RunInspectorPanel } from "./RunInspectorPanel";
import { TerminalPanel } from "./TerminalPanel";
import { Toolbar } from "./Toolbar";

type ProjectSessionPageProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
  events: BackendEvent[];
  chatState: ChatState;
  approvalState: ApprovalState;
  projectSessions: ProjectSessionSummary[];
  sessionError: string;
  extensions: ProjectExtensions;
  extensionError: string;
  backendError: string;
  onSend: (message: string) => void;
  onStop: () => void;
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
  onImportLegacy: () => void;
  onRefreshSessions: () => void;
  onResumeSession: (sessionId: string) => void;
  onRefreshExtensions: () => void;
  onInstallSkill: () => void;
  onInstallPlugin: () => void;
  onSettings: () => void;
};

export function ProjectSessionPage({
  t,
  projectPath,
  events,
  chatState,
  approvalState,
  projectSessions,
  sessionError,
  extensions,
  extensionError,
  backendError,
  onSend,
  onStop,
  onApprove,
  onReject,
  onImportLegacy,
  onRefreshSessions,
  onResumeSession,
  onRefreshExtensions,
  onInstallSkill,
  onInstallPlugin,
  onSettings,
}: ProjectSessionPageProps) {
  return (
    <div className="app-shell">
      <Toolbar t={t} projectPath={projectPath} onSettings={onSettings} />
      <div className="workspace-layout workspace-layout-polished">
        <ProjectSidebar
          t={t}
          projectPath={projectPath}
          projectSessions={projectSessions}
          sessionError={sessionError}
          extensions={extensions}
          extensionError={extensionError}
          onRefreshSessions={onRefreshSessions}
          onResumeSession={onResumeSession}
          onRefreshExtensions={onRefreshExtensions}
          onInstallSkill={onInstallSkill}
          onInstallPlugin={onInstallPlugin}
        />
        <main className="workspace-main" aria-label={t("workbench")}>
          <div className="project-strip">
            <span>{t("project")}</span>
            <strong>{projectPath}</strong>
            <span className={chatState.isRunning ? "status running" : "status"}>{chatState.isRunning ? t("running") : t("ready")}</span>
          </div>
          {backendError ? <div className="error-banner">{backendError}</div> : null}
          <LegacyImportPrompt t={t} events={events} onImport={onImportLegacy} />
          <ChatView t={t} chatState={chatState} onSend={onSend} onStop={onStop} />
          <TerminalPanel t={t} projectPath={projectPath} />
        </main>
        <RunInspectorPanel
          t={t}
          events={events}
          chatState={chatState}
          approvalState={approvalState}
          onApprove={onApprove}
          onReject={onReject}
        />
      </div>
    </div>
  );
}
