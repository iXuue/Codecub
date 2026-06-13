import { useEffect, useMemo, useState } from "react";
import { SettingsPage } from "./components/SettingsPage";
import { ProjectSessionPage } from "./components/ProjectSessionPage";
import { WelcomePage } from "./components/WelcomePage";
import { t, type Locale } from "./i18n";
import { parseBackendEventLine, type BackendEvent } from "./state/backendEvents";
import { applyApprovalEvent, createInitialApprovalState, markApprovalResolving } from "./state/approvalState";
import { applyBackendEvent, createInitialChatState } from "./state/chatState";
import type { RecentProject } from "./state/sessionIndex";

type View = "welcome" | "session" | "settings";
type ApprovalPolicy = "ask" | "auto" | "never";

export function App() {
  const [locale, setLocale] = useState<Locale>("zh-CN");
  const [view, setView] = useState<View>("welcome");
  const [projectPath, setProjectPath] = useState("");
  const [approvalPolicy, setApprovalPolicy] = useState<ApprovalPolicy>("ask");
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>([]);
  const [events, setEvents] = useState<BackendEvent[]>([]);
  const [chatState, setChatState] = useState(createInitialChatState());
  const [approvalState, setApprovalState] = useState(createInitialApprovalState());
  const [backendError, setBackendError] = useState("");
  const translate = useMemo(() => (key: Parameters<typeof t>[1]) => t(locale, key), [locale]);

  useEffect(() => {
    window.codecub.loadRecentProjects().then(setRecentProjects);
    window.codecub.loadSettings().then((settings) => {
      setLocale(settings.language);
      setApprovalPolicy(settings.approvalPolicy);
    });
    const removeEventListener = window.codecub.onBackendEvent((line) => {
      const event = parseBackendEventLine(line);
      setEvents((current) => [...current, event]);
      setChatState((current) => applyBackendEvent(current, event));
      setApprovalState((current) => applyApprovalEvent(current, event));
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
    setProjectPath(result.projectPath);
    setBackendError("");
    await window.codecub.startBackend(result.projectPath, approvalPolicy);
    setRecentProjects(await window.codecub.loadRecentProjects());
    setView("session");
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

  if (view === "settings") {
    return (
      <SettingsPage
        locale={locale}
        setLocale={setLocale}
        approvalPolicy={approvalPolicy}
        setApprovalPolicy={setApprovalPolicy}
        t={translate}
        onBack={() => setView(projectPath ? "session" : "welcome")}
      />
    );
  }

  if (view === "session") {
    return (
      <ProjectSessionPage
        t={translate}
        projectPath={projectPath}
        events={events}
        chatState={chatState}
        approvalState={approvalState}
        backendError={backendError}
        onSend={sendMessage}
        onStop={stopRun}
        onApprove={approveOperation}
        onReject={rejectOperation}
        onImportLegacy={importLegacyPico}
        onSettings={() => setView("settings")}
      />
    );
  }

  return (
    <WelcomePage
      t={translate}
      recentProjects={recentProjects}
      onOpenProject={openProject}
      onSettings={() => setView("settings")}
    />
  );
}
