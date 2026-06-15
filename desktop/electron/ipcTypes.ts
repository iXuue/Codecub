export type ModelProvider = "openai" | "anthropic" | "ollama";

export type CredentialStatus = {
  configured: boolean;
  source: "secure-store" | "environment" | "none";
  displayHint: string;
};

export type ProviderSettings = {
  provider: ModelProvider;
  model: string;
  baseUrl: string;
  host: string;
  credential: CredentialStatus;
};

export type SaveProviderSettingsRequest = {
  provider: ModelProvider;
  model: string;
  baseUrl: string;
  host: string;
  apiKey?: string;
  clearApiKey?: boolean;
};

export type AppSettings = {
  language: "zh-CN" | "en-US";
  approvalPolicy: "ask" | "auto" | "never";
  provider: ProviderSettings;
};

export type OpenProjectResult = {
  canceled: boolean;
  projectPath: string;
};

export type RecentProject = {
  path: string;
  name: string;
  lastSessionId: string;
  lastUsedAt: string;
};

export type BackendCommand =
  | { type: "send_message"; message: string; run_id?: string }
  | { type: "approve_operation"; run_id?: string; approval_id: string }
  | { type: "reject_operation"; run_id?: string; approval_id: string; reason?: string }
  | { type: "cancel_run"; run_id?: string }
  | { type: "import_legacy_pico"; session_id?: string }
  | { type: "close" };

export type TerminalStartRequest = {
  terminalId: string;
  cwd: string;
  cols: number;
  rows: number;
};

export type TerminalWriteRequest = {
  terminalId: string;
  data: string;
};

export type TerminalResizeRequest = {
  terminalId: string;
  cols: number;
  rows: number;
};

export type TerminalExitEvent = {
  terminalId: string;
  exitCode: number | null;
};

export type GitStatus = {
  branch: string;
  dirty: boolean;
  changedCount: number;
  ahead: number;
  behind: number;
  files: string[];
};
