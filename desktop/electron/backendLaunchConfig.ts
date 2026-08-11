import type { AppSettings } from "./ipcTypes.js";
import type { ModelProvider } from "./ipcTypes.js";

export type BackendLaunchConfig = {
  args: string[];
  env: NodeJS.ProcessEnv;
};

const apiKeyEnvNames: Partial<Record<ModelProvider, string>> = {
  openai: "OPENAI_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  kimi: "MOONSHOT_API_KEY",
  minimax: "MINIMAX_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
};

const providerEnvNames = [
  "CODECUB_PROVIDER",
  "OPENAI_API_KEY",
  "OPENAI_API_BASE",
  "OPENAI_MODEL",
  "DEEPSEEK_API_KEY",
  "DEEPSEEK_API_BASE",
  "DEEPSEEK_MODEL",
  "MOONSHOT_API_KEY",
  "MOONSHOT_API_BASE",
  "MOONSHOT_MODEL",
  "MINIMAX_API_KEY",
  "MINIMAX_API_BASE",
  "MINIMAX_MODEL",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_API_BASE",
  "ANTHROPIC_MODEL",
  "OLLAMA_HOST",
  "OLLAMA_MODEL",
];

export function buildBackendLaunchConfig(
  projectPath: string,
  settings: AppSettings,
  apiKey: string,
  baseEnv: NodeJS.ProcessEnv = process.env,
  resumeSessionId = "",
): BackendLaunchConfig {
  const args = ["--app-mode", "--cwd-b64", encodeUtf8Base64(projectPath), "--approval", settings.approvalPolicy];
  const env: NodeJS.ProcessEnv = { ...baseEnv };
  for (const name of providerEnvNames) {
    delete env[name];
  }
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  const provider = settings.provider.provider;

  if (resumeSessionId.trim()) {
    args.push("--resume", resumeSessionId.trim());
  }

  args.push("--provider", provider);
  args.push("--connection-profile-b64", encodeUtf8Base64(JSON.stringify({
    schema_version: 1,
    connection_profile_id: settings.provider.connectionProfileId || "",
    connection_type: settings.provider.connectionType || "custom",
    api_operator: settings.provider.apiOperator || "",
    model_vendor: settings.provider.modelVendor || provider,
    protocol: settings.provider.protocol || (provider === "anthropic" ? "anthropic-messages" : "openai-chat"),
    response_schema: settings.provider.responseSchema || "custom-unverified",
    credential_id: settings.provider.credentialId || "",
    endpoint_verification_status: settings.provider.endpointVerificationStatus || settings.provider.verificationStatus || "unverified",
    usage_schema_verification_status: settings.provider.usageSchemaVerificationStatus || "unverified",
  })));
  if (settings.provider.model.trim()) {
    args.push("--model", settings.provider.model.trim());
  }

  if (provider === "ollama") {
    if (settings.provider.host.trim()) {
      args.push("--host", settings.provider.host.trim());
    }
    return { args, env };
  }

  if (settings.provider.baseUrl.trim()) {
    args.push("--base-url", settings.provider.baseUrl.trim());
  }

  if (apiKey.trim()) {
    const envName = apiKeyEnvNames[provider];
    if (envName) {
      env[envName] = apiKey.trim();
    }
  }

  return { args, env };
}

export function encodeUtf8Base64(value: string): string {
  return Buffer.from(value, "utf-8").toString("base64");
}
