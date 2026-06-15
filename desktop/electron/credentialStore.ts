import keytar from "keytar";
import type { CredentialStatus, ModelProvider } from "./ipcTypes.js";

const SERVICE = "CodeCub Model API";

export function credentialAccount(provider: ModelProvider): string {
  return `codecub:${provider}:api-key`;
}

export function maskApiKey(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "not configured";
  }
  return `saved ending ${trimmed.slice(-4)}`;
}

export async function readApiKey(provider: ModelProvider): Promise<string> {
  return (await keytar.getPassword(SERVICE, credentialAccount(provider))) ?? "";
}

export async function saveApiKey(provider: ModelProvider, apiKey: string): Promise<CredentialStatus> {
  const trimmed = apiKey.trim();
  if (!trimmed) {
    await clearApiKey(provider);
    return { configured: false, source: "none", displayHint: "not configured" };
  }
  await keytar.setPassword(SERVICE, credentialAccount(provider), trimmed);
  return { configured: true, source: "secure-store", displayHint: maskApiKey(trimmed) };
}

export async function clearApiKey(provider: ModelProvider): Promise<CredentialStatus> {
  await keytar.deletePassword(SERVICE, credentialAccount(provider));
  return { configured: false, source: "none", displayHint: "not configured" };
}

export async function apiKeyStatus(provider: ModelProvider): Promise<CredentialStatus> {
  const saved = await readApiKey(provider);
  if (saved) {
    return { configured: true, source: "secure-store", displayHint: maskApiKey(saved) };
  }
  const envName = provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY";
  if (process.env[envName]) {
    return { configured: true, source: "environment", displayHint: envName };
  }
  return { configured: false, source: "none", displayHint: "not configured" };
}
