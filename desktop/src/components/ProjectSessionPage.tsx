import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ApprovalState } from "../state/approvalState";
import type { ChatState } from "../state/chatState";
import type { ProjectExtensions, ProjectSessionSummary } from "../../electron/ipcTypes";
import { ApprovalDialog } from "./ApprovalDialog";
import { ChatView } from "./ChatView";
import { DiffPreviewPanel } from "./DiffPreviewPanel";
import { ExtensionsPanel } from "./ExtensionsPanel";
import { GitStatusBadge } from "./GitStatusBadge";
import { LegacyImportPrompt } from "./LegacyImportPrompt";
import { ProjectHistoryPanel } from "./ProjectHistoryPanel";
import { RunLogSidebar } from "./RunLogSidebar";
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
      <div className="workspace-layout">
        <main className="workspace-main">
          <div className="project-strip">
            <span>{t("project")}</span>
            <strong>{projectPath}</strong>
            <GitStatusBadge t={t} projectPath={projectPath} />
            <span className={chatState.isRunning ? "status running" : "status"}>{chatState.isRunning ? t("running") : t("ready")}</span>
          </div>
          {backendError ? <div className="error-banner">{backendError}</div> : null}
          <LegacyImportPrompt t={t} events={events} onImport={onImportLegacy} />
          <ApprovalDialog t={t} approvals={approvalState.pending} onApprove={onApprove} onReject={onReject} />
          <ChatView t={t} chatState={chatState} onSend={onSend} onStop={onStop} />
          <TerminalPanel t={t} projectPath={projectPath} />
        </main>
        <aside className="workspace-side">
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
          <DiffPreviewPanel t={t} events={events} />
          <RunLogSidebar t={t} events={events} />
        </aside>
      </div>
    </div>
  );
}
