import { BrowserWindow, Menu, app, dialog, ipcMain } from "electron";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { BackendProcess } from "./backendProcess.js";
import { loadSettings, saveSettings } from "./appConfig.js";
import { readGitStatus } from "./gitStatus.js";
import { loadRecentProjects, rememberProject } from "./projectStore.js";
import { TerminalManager } from "./terminal.js";
import type { BackendCommand, TerminalResizeRequest, TerminalStartRequest, TerminalWriteRequest } from "./ipcTypes.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
let mainWindow: BrowserWindow | null = null;
const backend = new BackendProcess(join(__dirname, ".."));
const terminals = new TerminalManager();

function createWindow(): void {
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: "CodeCub",
    webPreferences: {
      preload: join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(join(__dirname, "../dist-renderer/index.html"));
  }
}

app.whenReady().then(() => {
  createWindow();

  backend.on("event", (line) => mainWindow?.webContents.send("backend:event", line));
  backend.on("error", (message) => mainWindow?.webContents.send("backend:error", message));
  backend.on("exit", (code) => mainWindow?.webContents.send("backend:error", `Backend exited: ${code ?? "unknown"}`));
  terminals.on("data", (terminalId, data) => mainWindow?.webContents.send("terminal:data", terminalId, data));
  terminals.on("exit", (terminalId, exitCode) => mainWindow?.webContents.send("terminal:exit", { terminalId, exitCode }));
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

ipcMain.handle("backend:start", async (_event, projectPath: string, approvalPolicy: "ask" | "auto" | "never" = "ask") => {
  backend.start(projectPath, approvalPolicy);
  await rememberProject(projectPath);
});

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

ipcMain.handle("settings:load", async () => loadSettings());
ipcMain.handle("settings:save", async (_event, settings) => saveSettings(settings));
ipcMain.handle("projects:recent", async () => loadRecentProjects());
