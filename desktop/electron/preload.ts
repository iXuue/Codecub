import { contextBridge, ipcRenderer } from "electron";
import type {
  AppSettings,
  BackendCommand,
  GitStatus,
  OpenProjectResult,
  RecentProject,
  TerminalExitEvent,
  TerminalResizeRequest,
  TerminalStartRequest,
  TerminalWriteRequest,
} from "./ipcTypes.js";

const api = {
  openProject: (): Promise<OpenProjectResult> => ipcRenderer.invoke("project:open"),
  startBackend: (projectPath: string, approvalPolicy: AppSettings["approvalPolicy"]): Promise<void> =>
    ipcRenderer.invoke("backend:start", projectPath, approvalPolicy),
  sendBackendCommand: (command: BackendCommand): Promise<void> => ipcRenderer.invoke("backend:send", command),
  stopBackend: (): Promise<void> => ipcRenderer.invoke("backend:stop"),
  loadSettings: (): Promise<AppSettings> => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings: AppSettings): Promise<AppSettings> => ipcRenderer.invoke("settings:save", settings),
  loadRecentProjects: (): Promise<RecentProject[]> => ipcRenderer.invoke("projects:recent"),
  loadGitStatus: (projectPath: string): Promise<GitStatus> => ipcRenderer.invoke("git:status", projectPath),
  startTerminal: (request: TerminalStartRequest): Promise<void> => ipcRenderer.invoke("terminal:start", request),
  writeTerminal: (request: TerminalWriteRequest): Promise<void> => ipcRenderer.invoke("terminal:write", request),
  resizeTerminal: (request: TerminalResizeRequest): Promise<void> => ipcRenderer.invoke("terminal:resize", request),
  closeTerminal: (terminalId: string): Promise<void> => ipcRenderer.invoke("terminal:close", terminalId),
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
