import type { AppSettings } from "./ipcTypes.js";

export type BackendLaunchConfig = {
  args: string[];
  env: NodeJS.ProcessEnv;
};

export function buildBackendLaunchConfig(
  projectPath: string,
  settings: AppSettings,
  apiKey: string,
  baseEnv: NodeJS.ProcessEnv = process.env,
): BackendLaunchConfig {
  const args = ["--app-mode", "--cwd", projectPath, "--approval", settings.approvalPolicy];
  const env: NodeJS.ProcessEnv = { ...baseEnv };
  const provider = settings.provider.provider;

  args.push("--provider", provider);
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
    if (provider === "anthropic") {
      env.ANTHROPIC_API_KEY = apiKey.trim();
    } else {
      env.OPENAI_API_KEY = apiKey.trim();
    }
  }

  return { args, env };
}
