import { BrowserWindow, Menu, app, dialog, ipcMain } from "electron";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildBackendLaunchConfig } from "./backendLaunchConfig.js";
import { BackendProcess, backendExitErrorMessage } from "./backendProcess.js";
import { loadSettings, saveSettings } from "./appConfig.js";
import { apiKeyStatus, clearApiKey, readApiKey, saveApiKey } from "./credentialStore.js";
import { readGitStatus } from "./gitStatus.js";
import { installProjectExtension, listProjectExtensions } from "./projectExtensions.js";
import { createProjectSession, deleteProjectSession, listProjectSessions, loadProjectSession } from "./projectSessions.js";
import { loadRecentProjects, rememberProject } from "./projectStore.js";
import { sendToRenderer } from "./safeIpc.js";
import { TerminalManager } from "./terminal.js";
import type {
  BackendCommand,
  ModelProvider,
  SaveProviderSettingsRequest,
  TerminalResizeRequest,
  TerminalStartRequest,
  TerminalWriteRequest,
} from "./ipcTypes.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
let mainWindow: BrowserWindow | null = null;
const backend = new BackendProcess(join(__dirname, ".."));
const terminals = new TerminalManager();

function createWindow(): void {
  Menu.setApplicationMenu(null);
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: "CodeCub",
    frame: false,
    webPreferences: {
      preload: join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = window;

  window.on("closed", () => {
    if (mainWindow === window) {
      mainWindow = null;
    }
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    window.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    window.loadFile(join(__dirname, "../dist-renderer/index.html"));
  }
}

app.whenReady().then(() => {
  createWindow();

  backend.on("event", (line) => sendToRenderer(mainWindow, "backend:event", line));
  backend.on("error", (message) => sendToRenderer(mainWindow, "backend:error", message));
  backend.on("exit", (event) => {
    const message = backendExitErrorMessage(event);
    if (message) {
      sendToRenderer(mainWindow, "backend:error", message);
    }
  });
  terminals.on("data", (terminalId, data) => sendToRenderer(mainWindow, "terminal:data", terminalId, data));
  terminals.on("exit", (terminalId, exitCode) => sendToRenderer(mainWindow, "terminal:exit", { terminalId, exitCode }));
  terminals.on("error", (terminalId, message) => sendToRenderer(mainWindow, "terminal:error", { terminalId, message }));
});

app.on("window-all-closed", () => {
  backend.stop();
  terminals.closeAll();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

ipcMain.handle("project:open", async () => {
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, {
        properties: ["openDirectory"],
      })
    : await dialog.showOpenDialog({
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) {
    return { canceled: true, projectPath: "" };
  }
  await rememberProject(result.filePaths[0]);
  return { canceled: false, projectPath: result.filePaths[0] };
});

ipcMain.handle(
  "backend:start",
  async (_event, projectPath: string, approvalPolicy: "ask" | "auto" | "never" = "ask", resumeSessionId = "") => {
    const settings = await loadSettings();
    const effectiveSettings = { ...settings, approvalPolicy };
    const credentialId = effectiveSettings.provider.credentialId || effectiveSettings.provider.provider;
    const apiKey = await readApiKey(credentialId, effectiveSettings.provider.provider);
    backend.start(buildBackendLaunchConfig(projectPath, effectiveSettings, apiKey, process.env, resumeSessionId));
    await rememberProject(projectPath);
  },
);

ipcMain.handle("backend:send", async (_event, command: BackendCommand) => {
  backend.send(command);
});

ipcMain.handle("backend:stop", async () => {
  backend.send({ type: "cancel_run" });
});

ipcMain.handle("terminal:start", async (_event, request: TerminalStartRequest) => {
  terminals.start(request);
});

ipcMain.handle("terminal:write", async (_event, request: TerminalWriteRequest) => {
  terminals.write(request);
});

ipcMain.handle("terminal:resize", async (_event, request: TerminalResizeRequest) => {
  terminals.resize(request);
});

ipcMain.handle("terminal:close", async (_event, terminalId: string) => {
  terminals.close(terminalId);
});

ipcMain.handle("git:status", async (_event, projectPath: string) => readGitStatus(projectPath));

ipcMain.handle("sessions:list", async (_event, projectPath: string) => listProjectSessions(projectPath));

ipcMain.handle("sessions:create", async (_event, projectPath: string) => createProjectSession(projectPath));

ipcMain.handle("sessions:load", async (_event, projectPath: string, sessionId: string) =>
  loadProjectSession(projectPath, sessionId),
);

ipcMain.handle("sessions:delete", async (_event, projectPath: string, sessionId: string) =>
  deleteProjectSession(projectPath, sessionId),
);

ipcMain.handle("extensions:list", async (_event, projectPath: string) => listProjectExtensions(projectPath));

ipcMain.handle("extensions:install-skill", async (_event, projectPath: string) => {
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] })
    : await dialog.showOpenDialog({ properties: ["openDirectory"] });
  if (result.canceled || !result.filePaths[0]) {
    return { canceled: true };
  }
  return installProjectExtension(projectPath, result.filePaths[0], "skill");
});

ipcMain.handle("extensions:install-plugin", async (_event, projectPath: string) => {
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] })
    : await dialog.showOpenDialog({ properties: ["openDirectory"] });
  if (result.canceled || !result.filePaths[0]) {
    return { canceled: true };
  }
  return installProjectExtension(projectPath, result.filePaths[0], "plugin");
});

ipcMain.handle("settings:load", async () => {
  const settings = await loadSettings();
  const credential = await apiKeyStatus(settings.provider.credentialId || settings.provider.provider, settings.provider.provider);
  return saveSettings({
    ...settings,
    provider: { ...settings.provider, credential },
  });
});
ipcMain.handle("settings:save", async (_event, settings) => saveSettings(settings));
ipcMain.handle("settings:provider-save", async (_event, request: SaveProviderSettingsRequest) => {
  const current = await loadSettings();
  const credentialId = request.credentialId || request.provider;
  let credential = current.provider.credential;
  if (request.clearApiKey) {
    credential = await clearApiKey(credentialId);
  } else if (request.apiKey && request.apiKey.trim()) {
    credential = await saveApiKey(credentialId, request.apiKey);
  } else {
    credential = await apiKeyStatus(credentialId, request.provider);
  }
  return saveSettings({
    ...current,
    provider: {
      provider: request.provider,
      model: request.model,
      baseUrl: request.baseUrl,
      host: request.host,
      credential,
      connectionProfileId: request.connectionProfileId,
      connectionType: request.connectionType,
      apiOperator: request.apiOperator,
      modelVendor: request.modelVendor,
      protocol: request.protocol,
      responseSchema: request.responseSchema,
      credentialId,
      verificationStatus: request.verificationStatus,
    },
  });
});
ipcMain.handle("settings:provider-clear-credential", async (_event, credentialId: string) => {
  const current = await loadSettings();
  const credential = await clearApiKey(credentialId);
  return saveSettings({
    ...current,
    provider: {
      ...current.provider,
      credential,
    },
  });
});
ipcMain.handle("projects:recent", async () => loadRecentProjects());

ipcMain.handle("window:minimize", async () => {
  mainWindow?.minimize();
});

ipcMain.handle("window:toggle-maximize", async () => {
  if (!mainWindow) {
    return;
  }
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
    return;
  }
  mainWindow.maximize();
});

ipcMain.handle("window:close", async () => {
  mainWindow?.close();
});
