import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { globalConfigDir } from "./appConfig.js";
import type { CredentialStatus } from "./ipcTypes.js";

type SecretsFile = {
  apiKeys?: Record<string, string>;
};

export function secretsPath(): string {
  return join(globalConfigDir(), "secrets.json");
}

export function maskApiKey(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "not configured";
  }
  return `saved ending ${trimmed.slice(-4)}`;
}

async function loadSecrets(): Promise<SecretsFile> {
  try {
    return JSON.parse(await readFile(secretsPath(), "utf-8")) as SecretsFile;
  } catch {
    return {};
  }
}

async function saveSecrets(secrets: SecretsFile): Promise<void> {
  await mkdir(globalConfigDir(), { recursive: true });
  await writeFile(secretsPath(), JSON.stringify({ apiKeys: secrets.apiKeys ?? {} }, null, 2), "utf-8");
}

export async function readApiKey(credentialId: string, legacyCredentialId = ""): Promise<string> {
  const secrets = await loadSecrets();
  return secrets.apiKeys?.[credentialId]?.trim() || secrets.apiKeys?.[legacyCredentialId]?.trim() || "";
}

export async function saveApiKey(credentialId: string, apiKey: string): Promise<CredentialStatus> {
  const trimmed = apiKey.trim();
  if (!trimmed) {
    return clearApiKey(credentialId);
  }
  const secrets = await loadSecrets();
  await saveSecrets({
    ...secrets,
    apiKeys: {
      ...(secrets.apiKeys ?? {}),
      [credentialId]: trimmed,
    },
  });
  return { configured: true, source: "global-file", displayHint: maskApiKey(trimmed) };
}

export async function clearApiKey(credentialId: string): Promise<CredentialStatus> {
  const secrets = await loadSecrets();
  const apiKeys = { ...(secrets.apiKeys ?? {}) };
  delete apiKeys[credentialId];
  await saveSecrets({ ...secrets, apiKeys });
  return { configured: false, source: "none", displayHint: "not configured" };
}

export async function apiKeyStatus(credentialId: string, legacyCredentialId = ""): Promise<CredentialStatus> {
  const saved = await readApiKey(credentialId, legacyCredentialId);
  if (saved) {
    return { configured: true, source: "global-file", displayHint: maskApiKey(saved) };
  }
  return { configured: false, source: "none", displayHint: "not configured" };
}
