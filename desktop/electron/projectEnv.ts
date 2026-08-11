import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { ModelProvider, ProviderSettings, SaveProviderSettingsRequest } from "./ipcTypes.js";

const PROVIDER_ENV_NAME = "CODECUB_PROVIDER";

const providerDefaults: Record<ModelProvider, ProviderSettings> = {
  openai: {
    provider: "openai",
    model: "qwen-flash",
    baseUrl: "https://www.right.codes/codex/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
  deepseek: {
    provider: "deepseek",
    model: "deepseek-chat",
    baseUrl: "https://api.deepseek.com",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
  kimi: {
    provider: "kimi",
    model: "moonshot-v1-8k",
    baseUrl: "https://api.moonshot.cn/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
  minimax: {
    provider: "minimax",
    model: "MiniMax-M3",
    baseUrl: "https://api.minimax.io/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
  anthropic: {
    provider: "anthropic",
    model: "claude-sonnet-4-6",
    baseUrl: "https://www.right.codes/claude/v1",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
  ollama: {
    provider: "ollama",
    model: "qwen3.5:4b",
    baseUrl: "",
    host: "http://127.0.0.1:11434",
    credential: { configured: false, source: "none", displayHint: "not configured" },
  },
};

type ProviderEnvKeys = {
  model: string;
  baseUrl?: string;
  host?: string;
  apiKey?: string;
};

const providerEnvKeys: Record<ModelProvider, ProviderEnvKeys> = {
  openai: { model: "OPENAI_MODEL", baseUrl: "OPENAI_API_BASE", apiKey: "OPENAI_API_KEY" },
  deepseek: { model: "DEEPSEEK_MODEL", baseUrl: "DEEPSEEK_API_BASE", apiKey: "DEEPSEEK_API_KEY" },
  kimi: { model: "MOONSHOT_MODEL", baseUrl: "MOONSHOT_API_BASE", apiKey: "MOONSHOT_API_KEY" },
  minimax: { model: "MINIMAX_MODEL", baseUrl: "MINIMAX_API_BASE", apiKey: "MINIMAX_API_KEY" },
  anthropic: { model: "ANTHROPIC_MODEL", baseUrl: "ANTHROPIC_API_BASE", apiKey: "ANTHROPIC_API_KEY" },
  ollama: { model: "OLLAMA_MODEL", host: "OLLAMA_HOST" },
};

export function projectEnvPath(projectPath: string): string {
  return join(projectPath, ".env");
}

export async function readProjectEnvText(projectPath: string): Promise<string> {
  try {
    return await readFile(projectEnvPath(projectPath), "utf-8");
  } catch (error) {
    if (isMissingFileError(error)) {
      return "";
    }
    throw error;
  }
}

function isMissingFileError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "code" in error && error.code === "ENOENT");
}

function isEnvKey(value: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(value);
}

function stripUnquotedComment(value: string): string {
  let inSingle = false;
  let inDouble = false;
  let escaped = false;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === "\\" && inDouble) {
      escaped = true;
      continue;
    }
    if (char === "'" && !inDouble) {
      inSingle = !inSingle;
      continue;
    }
    if (char === '"' && !inSingle) {
      inDouble = !inDouble;
      continue;
    }
    if (char === "#" && !inSingle && !inDouble && (index === 0 || /\s/.test(value[index - 1] ?? ""))) {
      return value.slice(0, index).trimEnd();
    }
  }
  return value.trim();
}

function unquoteEnvValue(value: string): string {
  const stripped = stripUnquotedComment(value.trim());
  if (stripped.length >= 2 && stripped[0] === stripped[stripped.length - 1] && (stripped[0] === '"' || stripped[0] === "'")) {
    return stripped.slice(1, -1);
  }
  return stripped;
}

function parseAssignment(line: string): { key: string; value: string } | null {
  let candidate = line.trim();
  if (!candidate || candidate.startsWith("#")) {
    return null;
  }
  if (candidate.startsWith("export ")) {
    candidate = candidate.slice("export ".length).trimStart();
  }
  const separatorIndex = candidate.indexOf("=");
  if (separatorIndex < 0) {
    return null;
  }
  const key = candidate.slice(0, separatorIndex).trim();
  if (!isEnvKey(key)) {
    return null;
  }
  return { key, value: unquoteEnvValue(candidate.slice(separatorIndex + 1)) };
}

export function parseProjectEnvText(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const assignment = parseAssignment(line);
    if (assignment) {
      values[assignment.key] = assignment.value;
    }
  }
  return values;
}

function formatEnvValue(value: string): string {
  if (/^[A-Za-z0-9_./:@-]*$/.test(value)) {
    return value;
  }
  return JSON.stringify(value);
}

export function updateProjectEnvText(text: string, updates: Record<string, string | null>): string {
  const lines = text ? text.replace(/\r\n/g, "\n").split("\n") : [];
  const seen = new Set<string>();
  const nextLines: string[] = [];

  for (const line of lines) {
    const assignment = parseAssignment(line);
    if (!assignment || !(assignment.key in updates)) {
      nextLines.push(line);
      continue;
    }
    seen.add(assignment.key);
    const nextValue = updates[assignment.key];
    if (nextValue !== null) {
      nextLines.push(`${assignment.key}=${formatEnvValue(nextValue)}`);
    }
  }

  const additions = Object.entries(updates)
    .filter(([key, value]) => value !== null && !seen.has(key))
    .map(([key, value]) => `${key}=${formatEnvValue(value ?? "")}`);

  const compactLines = nextLines.length === 1 && nextLines[0] === "" ? [] : nextLines;
  if (additions.length > 0) {
    if (compactLines.length > 0 && compactLines[compactLines.length - 1] !== "") {
      compactLines.push("");
    }
    compactLines.push(...additions);
  }
  const output = compactLines.join("\n").replace(/\n{3,}/g, "\n\n");
  return output ? `${output}\n` : "";
}

function normalizeProvider(value: string | undefined): ModelProvider | null {
  const provider = value?.trim().toLowerCase();
  if (
    provider === "openai" ||
    provider === "deepseek" ||
    provider === "kimi" ||
    provider === "minimax" ||
    provider === "anthropic" ||
    provider === "ollama"
  ) {
    return provider;
  }
  return null;
}

function inferProvider(values: Record<string, string>, fallback: ProviderSettings): ModelProvider {
  const explicit = normalizeProvider(values[PROVIDER_ENV_NAME]);
  if (explicit) {
    return explicit;
  }
  const hasOpenAI = Boolean(values.OPENAI_API_KEY || values.OPENAI_API_BASE || values.OPENAI_MODEL);
  const hasDeepSeek = Boolean(values.DEEPSEEK_API_KEY || values.DEEPSEEK_API_BASE || values.DEEPSEEK_MODEL);
  const hasKimi = Boolean(values.MOONSHOT_API_KEY || values.MOONSHOT_API_BASE || values.MOONSHOT_MODEL);
  const hasMiniMax = Boolean(values.MINIMAX_API_KEY || values.MINIMAX_API_BASE || values.MINIMAX_MODEL);
  const hasAnthropic = Boolean(values.ANTHROPIC_API_KEY || values.ANTHROPIC_API_BASE || values.ANTHROPIC_MODEL);
  const hasOllama = Boolean(values.OLLAMA_HOST || values.OLLAMA_MODEL);
  const providerCount = [hasOpenAI, hasDeepSeek, hasKimi, hasMiniMax, hasAnthropic, hasOllama].filter(Boolean).length;
  if (providerCount !== 1) {
    return fallback.provider;
  }
  if (hasDeepSeek) {
    return "deepseek";
  }
  if (hasKimi) {
    return "kimi";
  }
  if (hasMiniMax) {
    return "minimax";
  }
  if (hasAnthropic) {
    return "anthropic";
  }
  if (hasOllama) {
    return "ollama";
  }
  return fallback.provider;
}

function providerBase(provider: ModelProvider, fallback: ProviderSettings): ProviderSettings {
  if (fallback.provider === provider) {
    return fallback;
  }
  return providerDefaults[provider];
}

export function apiKeyFromProjectEnv(values: Record<string, string>, provider: ModelProvider): string {
  const keyName = providerEnvKeys[provider].apiKey;
  return keyName ? values[keyName]?.trim() ?? "" : "";
}

export function providerSettingsFromProjectEnv(values: Record<string, string>, fallback: ProviderSettings): ProviderSettings {
  const provider = inferProvider(values, fallback);
  const keys = providerEnvKeys[provider];
  const base = providerBase(provider, fallback);
  const apiKey = apiKeyFromProjectEnv(values, provider);
  return {
    provider,
    model: values[keys.model]?.trim() || base.model,
    baseUrl: keys.baseUrl ? values[keys.baseUrl]?.trim() || base.baseUrl : base.baseUrl,
    host: keys.host ? values[keys.host]?.trim() || base.host : base.host,
    credential: apiKey ? { configured: true, source: "project-env", displayHint: keys.apiKey ?? ".env" } : base.credential,
  };
}

export async function loadProjectEnvProviderSettings(projectPath: string, fallback: ProviderSettings): Promise<ProviderSettings> {
  return providerSettingsFromProjectEnv(parseProjectEnvText(await readProjectEnvText(projectPath)), fallback);
}

export async function readProjectEnvApiKey(projectPath: string, provider: ModelProvider): Promise<string> {
  return apiKeyFromProjectEnv(parseProjectEnvText(await readProjectEnvText(projectPath)), provider);
}

export async function saveProjectEnvProviderSettings(
  projectPath: string,
  request: SaveProviderSettingsRequest,
  fallback: ProviderSettings,
): Promise<ProviderSettings> {
  const existingText = await readProjectEnvText(projectPath);
  const keys = providerEnvKeys[request.provider];
  const updates: Record<string, string | null> = {
    [PROVIDER_ENV_NAME]: request.provider,
    [keys.model]: request.model.trim(),
  };

  if (keys.baseUrl) {
    updates[keys.baseUrl] = request.baseUrl.trim();
  }
  if (keys.host) {
    updates[keys.host] = request.host.trim();
  }
  if (keys.apiKey && request.clearApiKey) {
    updates[keys.apiKey] = null;
  } else if (keys.apiKey && request.apiKey?.trim()) {
    updates[keys.apiKey] = request.apiKey.trim();
  }

  const nextText = updateProjectEnvText(existingText, updates);
  await writeFile(projectEnvPath(projectPath), nextText, "utf-8");
  return providerSettingsFromProjectEnv(parseProjectEnvText(nextText), fallback);
}
