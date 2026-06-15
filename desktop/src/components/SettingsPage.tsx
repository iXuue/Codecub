import { useState, type Dispatch, type SetStateAction } from "react";
import type { I18nKey, Locale } from "../i18n";
import type { AppSettings, ModelProvider } from "../../electron/ipcTypes";

type ApprovalPolicy = "ask" | "auto" | "never";

type SettingsPageProps = {
  locale: Locale;
  setLocale: Dispatch<SetStateAction<Locale>>;
  approvalPolicy: ApprovalPolicy;
  setApprovalPolicy: Dispatch<SetStateAction<ApprovalPolicy>>;
  providerSettings: AppSettings["provider"] | null;
  setProviderSettings: Dispatch<SetStateAction<AppSettings["provider"] | null>>;
  t: (key: I18nKey) => string;
  onBack: () => void;
};

const providerDefaults: Record<ModelProvider, AppSettings["provider"]> = {
  openai: {
    provider: "openai",
    model: "qwen-flash",
    baseUrl: "https://www.right.codes/codex/v1",
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

export function SettingsPage({
  locale,
  setLocale,
  approvalPolicy,
  setApprovalPolicy,
  providerSettings,
  setProviderSettings,
  t,
  onBack,
}: SettingsPageProps) {
  const [apiKey, setApiKey] = useState("");
  const settings = providerSettings ?? providerDefaults.openai;

  const updateProviderSettings = (next: Partial<AppSettings["provider"]>) => {
    setProviderSettings({ ...settings, ...next });
  };

  const selectProvider = (provider: ModelProvider) => {
    setProviderSettings(providerDefaults[provider]);
    setApiKey("");
  };

  const saveSettings = async () => {
    const savedProviderSettings = await window.codecub.saveProviderSettings({
      provider: settings.provider,
      model: settings.model,
      baseUrl: settings.baseUrl,
      host: settings.host,
      apiKey,
    });
    const saved = await window.codecub.saveSettings({
      ...savedProviderSettings,
      language: locale,
      approvalPolicy,
    });
    setLocale(saved.language);
    setApprovalPolicy(saved.approvalPolicy);
    setProviderSettings(saved.provider);
    setApiKey("");
  };

  const clearApiKey = async () => {
    const saved = await window.codecub.clearProviderCredential(settings.provider);
    setProviderSettings(saved.provider);
    setApiKey("");
  };

  return (
    <div className="app-shell">
      <header className="toolbar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div className="brand-name">{t("settings")}</div>
        </div>
        <button className="button secondary" type="button" onClick={onBack}>
          {t("back")}
        </button>
      </header>
      <main className="settings-page">
        <section className="settings-section">
          <h2>{t("modelApiSettings")}</h2>
          <label>
            {t("provider")}
            <select value={settings.provider} onChange={(event) => selectProvider(event.target.value as ModelProvider)}>
              <option value="openai">{t("providerOpenAI")}</option>
              <option value="anthropic">{t("providerAnthropic")}</option>
              <option value="ollama">{t("providerOllama")}</option>
            </select>
          </label>
          <label>
            {t("model")}
            <input value={settings.model} onChange={(event) => updateProviderSettings({ model: event.target.value })} />
          </label>
          {settings.provider === "ollama" ? (
            <label>
              {t("host")}
              <input value={settings.host} onChange={(event) => updateProviderSettings({ host: event.target.value })} />
            </label>
          ) : (
            <label>
              {t("baseUrl")}
              <input
                value={settings.baseUrl}
                onChange={(event) => updateProviderSettings({ baseUrl: event.target.value })}
              />
            </label>
          )}
          {settings.provider === "ollama" ? (
            <div className="credential-status">{t("notRequiredForOllama")}</div>
          ) : (
            <>
              <label>
                {t("apiKey")}
                <input
                  type="password"
                  value={apiKey}
                  placeholder={t("apiKeyPlaceholder")}
                  onChange={(event) => setApiKey(event.target.value)}
                />
              </label>
              <div className="credential-row">
                <span>{t("credentialStatus")}</span>
                <strong>{settings.credential.displayHint}</strong>
              </div>
              <button className="button secondary" type="button" onClick={clearApiKey}>
                {t("clearApiKey")}
              </button>
            </>
          )}
        </section>

        <section className="settings-section">
          <label>
            {t("approvalPolicy")}
            <select value={approvalPolicy} onChange={(event) => setApprovalPolicy(event.target.value as ApprovalPolicy)}>
              <option value="ask">ask</option>
              <option value="auto">auto</option>
              <option value="never">never</option>
            </select>
          </label>
          <label>
            {t("language")}
            <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
              <option value="zh-CN">中文</option>
              <option value="en-US">English</option>
            </select>
          </label>
          <button className="button primary" type="button" onClick={saveSettings}>
            {t("save")}
          </button>
        </section>
      </main>
    </div>
  );
}
