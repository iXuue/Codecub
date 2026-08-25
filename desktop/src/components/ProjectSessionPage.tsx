import { useRef } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ApprovalState } from "../state/approvalState";
import type { ChatState } from "../state/chatState";
import type { ProjectExtensions, ProjectSessionSummary } from "../../electron/ipcTypes";
import type { UsageState } from "../state/usageState";
import { gsap, motionAllowed, useGSAP } from "../motion/gsapSetup";
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
  activeSessionId: string;
  sessionError: string;
  extensions: ProjectExtensions;
  extensionError: string;
  backendError: string;
  usageState: UsageState;
  onSend: (message: string) => void;
  onStop: () => void;
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
  onImportLegacy: () => void;
  onRefreshSessions: () => void;
  onResumeSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  onRefreshExtensions: () => void;
  onInstallSkill: () => void;
  onInstallPlugin: () => void;
  onSettings: () => void;
  onBackHome: () => void;
};

export function ProjectSessionPage({
  t,
  projectPath,
  events,
  chatState,
  approvalState,
  projectSessions,
  activeSessionId,
  sessionError,
  extensions,
  extensionError,
  backendError,
  usageState,
  onSend,
  onStop,
  onApprove,
  onReject,
  onImportLegacy,
  onRefreshSessions,
  onResumeSession,
  onCreateSession,
  onDeleteSession,
  onRefreshExtensions,
  onInstallSkill,
  onInstallPlugin,
  onSettings,
  onBackHome,
}: ProjectSessionPageProps) {
  const layoutRef = useRef<HTMLDivElement | null>(null);
  const projectName = projectPath.split(/[\\/]+/).filter(Boolean).at(-1) ?? projectPath;

  useGSAP(
    () => {
      if (!motionAllowed()) {
        return;
      }
      gsap.from(".workspace-column", {
        autoAlpha: 0,
        y: 14,
        duration: 0.42,
        ease: "power2.out",
        stagger: 0.06,
      });
    },
    { scope: layoutRef },
  );

  return (
    <div className="app-shell">
      <Toolbar t={t} projectPath={projectPath} onSettings={onSettings} onBack={onBackHome} backLabel={t("home")} />
      <div className="workspace-layout workspace-layout-polished" ref={layoutRef}>
        <ProjectSidebar
          t={t}
          projectPath={projectPath}
          projectSessions={projectSessions}
          activeSessionId={activeSessionId}
          sessionError={sessionError}
          extensions={extensions}
          extensionError={extensionError}
          onRefreshSessions={onRefreshSessions}
          onResumeSession={onResumeSession}
          onCreateSession={onCreateSession}
          onDeleteSession={onDeleteSession}
          onRefreshExtensions={onRefreshExtensions}
          onInstallSkill={onInstallSkill}
          onInstallPlugin={onInstallPlugin}
        />
        <main className="workspace-main workspace-column" aria-label={t("workbench")}>
          <div className="project-strip">
            <div className="project-strip-main">
              <span>{t("workbench")}</span>
              <strong>{projectName}</strong>
              <small>{projectPath}</small>
            </div>
            <span className={chatState.isRunning ? "status running" : "status"}>
              <span className="status-dot" />
              {chatState.isRunning ? t("running") : t("ready")}
            </span>
          </div>
          {backendError ? <div className="error-banner">{backendError}</div> : null}
          <LegacyImportPrompt t={t} events={events} onImport={onImportLegacy} />
          <ChatView t={t} chatState={chatState} events={events} activeSessionId={activeSessionId} onSend={onSend} onStop={onStop} />
          <TerminalPanel t={t} projectPath={projectPath} />
        </main>
        <RunInspectorPanel
          t={t}
          events={events}
          approvalState={approvalState}
          onApprove={onApprove}
          onReject={onReject}
          usageState={usageState}
        />
      </div>
    </div>
  );
}
