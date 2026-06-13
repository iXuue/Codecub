import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ApprovalState } from "../state/approvalState";
import type { ChatState } from "../state/chatState";
import { ApprovalDialog } from "./ApprovalDialog";
import { ChatView } from "./ChatView";
import { DiffPreviewPanel } from "./DiffPreviewPanel";
import { GitStatusBadge } from "./GitStatusBadge";
import { LegacyImportPrompt } from "./LegacyImportPrompt";
import { RunLogSidebar } from "./RunLogSidebar";
import { TerminalPanel } from "./TerminalPanel";
import { Toolbar } from "./Toolbar";

type ProjectSessionPageProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
  events: BackendEvent[];
  chatState: ChatState;
  approvalState: ApprovalState;
  backendError: string;
  onSend: (message: string) => void;
  onStop: () => void;
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
  onImportLegacy: () => void;
  onSettings: () => void;
};

export function ProjectSessionPage({
  t,
  projectPath,
  events,
  chatState,
  approvalState,
  backendError,
  onSend,
  onStop,
  onApprove,
  onReject,
  onImportLegacy,
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
          <DiffPreviewPanel t={t} events={events} />
          <RunLogSidebar t={t} events={events} />
        </aside>
      </div>
    </div>
  );
}
