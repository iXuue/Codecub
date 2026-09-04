import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { SettingsPage } from "./components/SettingsPage";
import { ProjectSessionPage } from "./components/ProjectSessionPage";
import { WelcomePage } from "./components/WelcomePage";
import { t, type Locale } from "./i18n";
import { parseBackendEventLine, type BackendEvent } from "./state/backendEvents";
import { applyApprovalEvent, createInitialApprovalState, markApprovalResolving } from "./state/approvalState";
import { applyBackendEvent, createChatStateFromSession, createInitialChatState } from "./state/chatState";
import { applyUsageEvent, createInitialUsageState } from "./state/usageState";
import type { RecentProject } from "./state/sessionIndex";
import type { AppSettings, ProjectExtensions, ProjectSessionSummary } from "../electron/ipcTypes";

type View = "welcome" | "session" | "settings";
type ApprovalPolicy = "ask" | "auto" | "never";
type ExecutionMode = "single" | "multi_agent";
const defaultAppearance: AppSettings["appearance"] = { themeMode: "dark", accentColor: "#38BDF8" };

export function App() {
  const [locale, setLocale] = useState<Locale>("zh-CN");
  const [view, setView] = useState<View>("welcome");
  const [projectPath, setProjectPath] = useState("");
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>("ask");
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("single");
  const [providerSettings, setProviderSettings] = useState<AppSettings["provider"] | null>(null);
  const [appearanceSettings, setAppearanceSettings] = useState<AppSettings["appearance"]>(defaultAppearance);
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>([]);
  const [projectSessions, setProjectSessions] = useState<ProjectSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [sessionError, setSessionError] = useState("");
  const [extensions, setExtensions] = useState<ProjectExtensions>({ skills: [], plugins: [] });
  const [extensionError, setExtensionError] = useState("");
  const [events, setEvents] = useState<BackendEvent[]>([]);
  const [chatState, setChatState] = useState(createInitialChatState());
  const [approvalState, setApprovalState] = useState(createInitialApprovalState());
  const [backendError, setBackendError] = useState("");
  const [usageState, setUsageState] = useState(createInitialUsageState());
  const translate = useMemo(() => (key: Parameters<typeof t>[1]) => t(locale, key), [locale]);

  useEffect(() => {
    window.codecub.loadRecentProjects().then(setRecentProjects);
    window.codecub.loadSettings().then((settings) => {
      setLocale(settings.language);
      setApprovalPolicy(settings.approvalPolicy);
      setExecutionMode(settings.executionMode ?? "single");
      setProviderSettings(settings.provider);
      setAppearanceSettings(settings.appearance ?? defaultAppearance);
    });
    const removeEventListener = window.codecub.onBackendEvent((line) => {
      const event = parseBackendEventLine(line);
      setEvents((current) => [...current, event]);
      if (event.type === "session_started") {
        setActiveSessionId(event.session_id);
      }
      setChatState((current) => applyBackendEvent(current, event));
      setApprovalState((current) => applyApprovalEvent(current, event));
      setUsageState((current) => applyUsageEvent(current, event));
    });
    const removeErrorListener = window.codecub.onBackendError((message) => {
      setBackendError(message);
    });
    return () => {
      removeEventListener();
      removeErrorListener();
    };
  }, []);

  async function openProject() {
    const result = await window.codecub.openProject();
    if (result.canceled) {
      return;
    }
    await enterProject(result.projectPath);
  }

  async function refreshProjectSessions(targetProjectPath = projectPath) {
    if (!targetProjectPath) {
      return;
    }
    try {
      setProjectSessions(await window.codecub.listProjectSessions(targetProjectPath));
      setSessionError("");
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : String(error));
    }
  }

  async function refreshProjectExtensions(targetProjectPath = projectPath, clearError = true) {
    if (!targetProjectPath) {
      return;
    }
    try {
      setExtensions(await window.codecub.listProjectExtensions(targetProjectPath));
      if (clearError) {
        setExtensionError("");
      }
    } catch (error) {
      setExtensionError(error instanceof Error ? error.message : String(error));
    }
  }

  async function enterProject(nextProjectPath: string, resumeSessionId = "") {
    setProjectPath(nextProjectPath);
    setBackendError("");
    setSessionError("");
    setExtensionError("");
    setEvents([]);
    setActiveSessionId(resumeSessionId);
    if (resumeSessionId) {
      try {
        setChatState(createChatStateFromSession(await window.codecub.loadProjectSession(nextProjectPath, resumeSessionId)));
      } catch (error) {
        setChatState(createInitialChatState());
        setSessionError(error instanceof Error ? error.message : String(error));
      }
    } else {
      setChatState(createInitialChatState());
    }
    setApprovalState(createInitialApprovalState());
    setUsageState(createInitialUsageState());
    setView("session");
    try {
      await window.codecub.startBackend(nextProjectPath, approvalPolicy, executionMode, resumeSessionId);
      setRecentProjects(await window.codecub.loadRecentProjects());
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setBackendError(message);
      setRecentProjects(await window.codecub.loadRecentProjects());
    }
    await refreshProjectSessions(nextProjectPath);
    await refreshProjectExtensions(nextProjectPath);
  }

  async function openSettings() {
    setView("settings");
  }

  async function openRecentProject(nextProjectPath: string) {
    if (!nextProjectPath) {
      return;
    }
    await enterProject(nextProjectPath);
  }

  async function resumeSession(sessionId: string) {
    if (!projectPath) {
      return;
    }
    await enterProject(projectPath, sessionId);
  }

  async function createSession() {
    if (!projectPath || chatState.isRunning) {
      return;
    }
    const session = await window.codecub.createProjectSession(projectPath);
    await enterProject(projectPath, session.id);
  }

  async function deleteSession(sessionId: string) {
    if (!projectPath || chatState.isRunning) {
      return;
    }
    if (!window.confirm(translate("deleteChatConfirm"))) {
      return;
    }
    await window.codecub.deleteProjectSession(projectPath, sessionId);
    if (sessionId === activeSessionId) {
      await enterProject(projectPath);
      return;
    }
    await refreshProjectSessions(projectPath);
  }

  async function sendMessage(message: string) {
    setBackendError("");
    await window.codecub.sendBackendCommand({ type: "send_message", message });
  }

  async function stopRun() {
    await window.codecub.sendBackendCommand({ type: "cancel_run", run_id: chatState.activeRunId });
  }

  async function approveOperation(approvalId: string, runId: string) {
    setApprovalState((current) => markApprovalResolving(current, approvalId));
    await window.codecub.sendBackendCommand({ type: "approve_operation", run_id: runId, approval_id: approvalId });
  }

  async function rejectOperation(approvalId: string, runId: string) {
    setApprovalState((current) => markApprovalResolving(current, approvalId));
    await window.codecub.sendBackendCommand({
      type: "reject_operation",
      run_id: runId,
      approval_id: approvalId,
      reason: "user_rejected",
    });
  }

  async function importLegacyPico() {
    await window.codecub.sendBackendCommand({ type: "import_legacy_pico" });
  }

  async function installSkill() {
    if (!projectPath) {
      return;
    }
    const result = await window.codecub.installProjectSkill(projectPath);
    if (!result.canceled && result.error) {
      setExtensionError(result.error);
      await refreshProjectExtensions(projectPath, false);
      return;
    } else if (!result.canceled) {
      setExtensionError("");
    }
    await refreshProjectExtensions(projectPath);
  }

  async function installPlugin() {
    if (!projectPath) {
      return;
    }
    const result = await window.codecub.installProjectPlugin(projectPath);
    if (!result.canceled && result.error) {
      setExtensionError(result.error);
      await refreshProjectExtensions(projectPath, false);
      return;
    } else if (!result.canceled) {
      setExtensionError("");
    }
    await refreshProjectExtensions(projectPath);
  }

  const themed = (content: ReactNode) => (
    <div
      className="theme-root"
      data-theme={appearanceSettings.themeMode}
      style={{ "--color-accent-user": appearanceSettings.accentColor } as CSSProperties}
    >
      {content}
    </div>
  );

  if (view === "settings") {
    return themed(
      <SettingsPage
        locale={locale}
        setLocale={setLocale}
        approvalPolicy={approvalPolicy}
        setApprovalPolicy={setApprovalPolicy}
        executionMode={executionMode}
        setExecutionMode={setExecutionMode}
        providerSettings={providerSettings}
        setProviderSettings={setProviderSettings}
        appearanceSettings={appearanceSettings}
        setAppearanceSettings={setAppearanceSettings}
        t={translate}
        onBack={() => setView(projectPath ? "session" : "welcome")}
      />,
    );
  }

  if (view === "session") {
    return themed(
      <ProjectSessionPage
        t={translate}
        projectPath={projectPath}
        events={events}
        chatState={chatState}
        approvalState={approvalState}
        projectSessions={projectSessions}
        activeSessionId={activeSessionId}
        sessionError={sessionError}
        extensions={extensions}
        extensionError={extensionError}
        backendError={backendError}
        usageState={usageState}
        onSend={sendMessage}
        onStop={stopRun}
        onApprove={approveOperation}
        onReject={rejectOperation}
        onImportLegacy={importLegacyPico}
        onRefreshSessions={() => refreshProjectSessions()}
        onResumeSession={resumeSession}
        onCreateSession={createSession}
        onDeleteSession={deleteSession}
        onRefreshExtensions={() => refreshProjectExtensions()}
        onInstallSkill={installSkill}
        onInstallPlugin={installPlugin}
        onSettings={openSettings}
        onBackHome={() => setView("welcome")}
      />,
    );
  }

  return themed(
    <WelcomePage
      t={translate}
      recentProjects={recentProjects}
      onOpenProject={openProject}
      onOpenRecentProject={openRecentProject}
      onSettings={openSettings}
    />,
  );
}
