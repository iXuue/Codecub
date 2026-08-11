import { app } from "electron";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { createHash } from "node:crypto";
import type { AppSettings, AppearanceSettings, ProviderSettings } from "./ipcTypes.js";

export const GLOBAL_CONFIG_DIR_NAME = "codecub-global";

export const defaultProviderSettings: ProviderSettings = {
  provider: "openai",
  model: "gpt-5.4",
  baseUrl: "https://www.right.codes/codex/v1",
  host: "http://127.0.0.1:11434",
  credential: {
    configured: false,
    source: "none",
    displayHint: "not configured",
  },
  connectionProfileId: "rightcode-codex",
  connectionType: "relay",
  apiOperator: "right.codes",
  modelVendor: "openai",
  protocol: "openai-responses",
  responseSchema: "rightcode-codex-unverified",
  credentialId: "rightcode",
  verificationStatus: "unverified",
};

export const defaultAppearanceSettings: AppearanceSettings = {
  themeMode: "dark",
  accentColor: "#38BDF8",
};

export const defaultSettings: AppSettings = {
  language: "zh-CN",
  approvalPolicy: "ask",
  provider: defaultProviderSettings,
  appearance: defaultAppearanceSettings,
};

export function globalConfigDir(): string {
  const override = process.env.CODECUB_GLOBAL_DIR?.trim();
  if (override) {
    return override;
  }
  return join(dirname(app.getPath("exe")), GLOBAL_CONFIG_DIR_NAME);
}

export function settingsPath(): string {
  return join(globalConfigDir(), "settings.json");
}

function legacySettingsPath(): string {
  return join(app.getPath("userData"), "settings.json");
}

async function readSettingsFile(path: string): Promise<AppSettings> {
  const raw = await readFile(path, "utf-8");
  const parsed = JSON.parse(raw) as Partial<AppSettings>;
  const parsedProvider: Partial<ProviderSettings> = parsed.provider ?? {};
  const migratedConnection = parsedProvider.connectionProfileId ? {} : inferLegacyConnection(parsedProvider);
  return {
    ...defaultSettings,
    ...parsed,
    provider: {
      ...defaultProviderSettings,
      ...parsedProvider,
      ...migratedConnection,
      credential: {
        ...defaultProviderSettings.credential,
        ...(parsed.provider?.credential ?? {}),
      },
    },
    appearance: {
      ...defaultAppearanceSettings,
      ...(parsed.appearance ?? {}),
    },
  } as AppSettings;
}

function inferLegacyConnection(provider: Partial<ProviderSettings>): Partial<ProviderSettings> {
  const baseUrl = String(provider.baseUrl ?? "").toLowerCase();
  const modelProvider = provider.provider ?? "openai";
  const rightCode = /(^|\.)right\.codes|(^|\.)rightapi\.ai/.test(new URL(baseUrl || "https://invalid.local").hostname);
  if (rightCode && baseUrl.includes("/claude/")) {
    return { connectionProfileId: "rightcode-claude", connectionType: "relay", apiOperator: "right.codes", modelVendor: "anthropic", protocol: "anthropic-messages", responseSchema: "rightcode-claude-unverified", credentialId: "rightcode", verificationStatus: "unverified" };
  }
  if (rightCode) {
    return { connectionProfileId: "rightcode-codex", connectionType: "relay", apiOperator: "right.codes", modelVendor: "openai", protocol: "openai-responses", responseSchema: "rightcode-codex-unverified", credentialId: "rightcode", verificationStatus: "unverified" };
  }
  if (modelProvider === "ollama") {
    return { connectionProfileId: "ollama-local", connectionType: "local", apiOperator: "local", modelVendor: "ollama", protocol: "ollama-generate", responseSchema: "ollama", credentialId: "ollama-local", verificationStatus: "verified" };
  }
  const officialHosts: Partial<Record<typeof modelProvider, string>> = { openai: "api.openai.com", anthropic: "api.anthropic.com", deepseek: "api.deepseek.com", kimi: "api.moonshot.cn", minimax: "api.minimax.io" };
  const host = new URL(baseUrl || "https://invalid.local").hostname;
  const official = officialHosts[modelProvider] === host;
  const suffix = official ? "official" : createHash("sha256").update(`${modelProvider}|${baseUrl}`).digest("hex").slice(0, 12);
  return {
    connectionProfileId: official ? `${modelProvider}-official` : `custom-${suffix}`,
    connectionType: official ? "direct" : "custom",
    apiOperator: official ? modelProvider : host || "custom",
    modelVendor: modelProvider,
    protocol: modelProvider === "anthropic" ? "anthropic-messages" : "openai-chat",
    responseSchema: official ? `${modelProvider}-official` : "custom-unverified",
    credentialId: official ? `${modelProvider}-official` : `custom:${suffix}`,
    verificationStatus: official ? "verified" : "unverified",
  };
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    return await readSettingsFile(settingsPath());
  } catch {
    try {
      return await readSettingsFile(legacySettingsPath());
    } catch {
      return defaultSettings;
    }
  }
}

export function sanitizeSettingsForDisk(settings: AppSettings): AppSettings {
  const appearance = settings.appearance ?? defaultAppearanceSettings;
  return {
    language: settings.language,
    approvalPolicy: settings.approvalPolicy,
    appearance: {
      themeMode: appearance.themeMode === "light" ? "light" : "dark",
      accentColor: appearance.accentColor || defaultAppearanceSettings.accentColor,
    },
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
      connectionProfileId: settings.provider.connectionProfileId,
      connectionType: settings.provider.connectionType,
      apiOperator: settings.provider.apiOperator,
      modelVendor: settings.provider.modelVendor,
      protocol: settings.provider.protocol,
      responseSchema: settings.provider.responseSchema,
      credentialId: settings.provider.credentialId,
      verificationStatus: settings.provider.verificationStatus,
      endpointVerificationStatus: settings.provider.endpointVerificationStatus || settings.provider.verificationStatus || "unverified",
      usageSchemaVerificationStatus: settings.provider.usageSchemaVerificationStatus || "unverified",
    },
  };
}

export async function saveSettings(settings: AppSettings): Promise<AppSettings> {
  await mkdir(globalConfigDir(), { recursive: true });
  const safeSettings = sanitizeSettingsForDisk(settings);
  await writeFile(settingsPath(), JSON.stringify(safeSettings, null, 2), "utf-8");
  return safeSettings;
}
