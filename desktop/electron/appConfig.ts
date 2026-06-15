import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { AppSettings, ProviderSettings } from "./ipcTypes.js";

export const defaultProviderSettings: ProviderSettings = {
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://www.right.codes/codex/v1",
  host: "http://127.0.0.1:11434",
  credential: {
    configured: false,
    source: "none",
    displayHint: "not configured",
  },
};

export const defaultSettings: AppSettings = {
  language: "zh-CN",
  approvalPolicy: "ask",
  provider: defaultProviderSettings,
};

function settingsPath(): string {
  return join(app.getPath("userData"), "settings.json");
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    const raw = await readFile(settingsPath(), "utf-8");
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    return {
      ...defaultSettings,
      ...parsed,
      provider: {
        ...defaultProviderSettings,
        ...(parsed.provider ?? {}),
        credential: {
          ...defaultProviderSettings.credential,
          ...(parsed.provider?.credential ?? {}),
        },
      },
    } as AppSettings;
  } catch {
    return defaultSettings;
  }
}

export function sanitizeSettingsForDisk(settings: AppSettings): AppSettings {
  return {
    language: settings.language,
    approvalPolicy: settings.approvalPolicy,
    provider: {
      provider: settings.provider.provider,
      model: settings.provider.model,
      baseUrl: settings.provider.baseUrl,
      host: settings.provider.host,
      credential: {
        configured: settings.provider.credential.configured,
        source: settings.provider.credential.source,
        displayHint: settings.provider.credential.displayHint,
      },
    },
  };
}

export async function saveSettings(settings: AppSettings): Promise<AppSettings> {
  await mkdir(app.getPath("userData"), { recursive: true });
  const safeSettings = sanitizeSettingsForDisk(settings);
  await writeFile(settingsPath(), JSON.stringify(safeSettings, null, 2), "utf-8");
  return safeSettings;
}
