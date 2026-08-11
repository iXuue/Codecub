import { contextBridge, ipcRenderer } from "electron";
import type {
  AppSettings,
  BackendCommand,
  CreateProjectSessionResult,
  DeleteProjectSessionResult,
  GitStatus,
  InstallProjectExtensionResult,
  OpenProjectResult,
  ProjectExtensions,
  ProjectSessionDetail,
  ProjectSessionSummary,
  RecentProject,
  SaveProviderSettingsRequest,
  TerminalErrorEvent,
  TerminalExitEvent,
  TerminalResizeRequest,
  TerminalStartRequest,
  TerminalWriteRequest,
} from "./ipcTypes.js";

const api = {
  openProject: (): Promise<OpenProjectResult> => ipcRenderer.invoke("project:open"),
  startBackend: (projectPath: string, approvalPolicy: AppSettings["approvalPolicy"], resumeSessionId = ""): Promise<void> =>
    ipcRenderer.invoke("backend:start", projectPath, approvalPolicy, resumeSessionId),
  sendBackendCommand: (command: BackendCommand): Promise<void> => ipcRenderer.invoke("backend:send", command),
  stopBackend: (): Promise<void> => ipcRenderer.invoke("backend:stop"),
  loadSettings: (): Promise<AppSettings> => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings: AppSettings): Promise<AppSettings> => ipcRenderer.invoke("settings:save", settings),
  saveProviderSettings: (request: SaveProviderSettingsRequest): Promise<AppSettings> =>
    ipcRenderer.invoke("settings:provider-save", request),
  clearProviderCredential: (credentialId: string): Promise<AppSettings> =>
    ipcRenderer.invoke("settings:provider-clear-credential", credentialId),
  loadRecentProjects: (): Promise<RecentProject[]> => ipcRenderer.invoke("projects:recent"),
  listProjectSessions: (projectPath: string): Promise<ProjectSessionSummary[]> =>
    ipcRenderer.invoke("sessions:list", projectPath),
  createProjectSession: (projectPath: string): Promise<CreateProjectSessionResult> =>
    ipcRenderer.invoke("sessions:create", projectPath),
  loadProjectSession: (projectPath: string, sessionId: string): Promise<ProjectSessionDetail> =>
    ipcRenderer.invoke("sessions:load", projectPath, sessionId),
  deleteProjectSession: (projectPath: string, sessionId: string): Promise<DeleteProjectSessionResult> =>
    ipcRenderer.invoke("sessions:delete", projectPath, sessionId),
  listProjectExtensions: (projectPath: string): Promise<ProjectExtensions> =>
    ipcRenderer.invoke("extensions:list", projectPath),
  installProjectSkill: (projectPath: string): Promise<InstallProjectExtensionResult> =>
    ipcRenderer.invoke("extensions:install-skill", projectPath),
  installProjectPlugin: (projectPath: string): Promise<InstallProjectExtensionResult> =>
    ipcRenderer.invoke("extensions:install-plugin", projectPath),
  loadGitStatus: (projectPath: string): Promise<GitStatus> => ipcRenderer.invoke("git:status", projectPath),
  startTerminal: (request: TerminalStartRequest): Promise<void> => ipcRenderer.invoke("terminal:start", request),
  writeTerminal: (request: TerminalWriteRequest): Promise<void> => ipcRenderer.invoke("terminal:write", request),
  resizeTerminal: (request: TerminalResizeRequest): Promise<void> => ipcRenderer.invoke("terminal:resize", request),
  closeTerminal: (terminalId: string): Promise<void> => ipcRenderer.invoke("terminal:close", terminalId),
  minimizeWindow: (): Promise<void> => ipcRenderer.invoke("window:minimize"),
  toggleMaximizeWindow: (): Promise<void> => ipcRenderer.invoke("window:toggle-maximize"),
  closeWindow: (): Promise<void> => ipcRenderer.invoke("window:close"),
  onTerminalData: (callback: (terminalId: string, data: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, terminalId: string, data: string) => callback(terminalId, data);
    ipcRenderer.on("terminal:data", listener);
    return () => ipcRenderer.off("terminal:data", listener);
  },
  onTerminalExit: (callback: (event: TerminalExitEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, event: TerminalExitEvent) => callback(event);
    ipcRenderer.on("terminal:exit", listener);
    return () => ipcRenderer.off("terminal:exit", listener);
  },
  onTerminalError: (callback: (event: TerminalErrorEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, event: TerminalErrorEvent) => callback(event);
    ipcRenderer.on("terminal:error", listener);
    return () => ipcRenderer.off("terminal:error", listener);
  },
  onBackendEvent: (callback: (line: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, line: string) => callback(line);
    ipcRenderer.on("backend:event", listener);
    return () => ipcRenderer.off("backend:event", listener);
  },
  onBackendError: (callback: (message: string) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, message: string) => callback(message);
    ipcRenderer.on("backend:error", listener);
    return () => ipcRenderer.off("backend:error", listener);
  },
};

contextBridge.exposeInMainWorld("codecub", api);

declare global {
  interface Window {
    codecub: typeof api;
  }
}
