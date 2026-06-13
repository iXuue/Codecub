import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { AppSettings } from "./ipcTypes.js";

export const defaultSettings: AppSettings = {
  language: "zh-CN",
  approvalPolicy: "ask",
};

function settingsPath(): string {
  return join(app.getPath("userData"), "settings.json");
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    const raw = await readFile(settingsPath(), "utf-8");
    return { ...defaultSettings, ...JSON.parse(raw) } as AppSettings;
  } catch {
    return defaultSettings;
  }
}

export async function saveSettings(settings: AppSettings): Promise<AppSettings> {
  await mkdir(app.getPath("userData"), { recursive: true });
  // P0 keeps provider secrets in the parent process environment only; do not persist API keys here.
  const safeSettings: AppSettings = {
    language: settings.language,
    approvalPolicy: settings.approvalPolicy,
  };
  await writeFile(settingsPath(), JSON.stringify(safeSettings, null, 2), "utf-8");
  return safeSettings;
}
