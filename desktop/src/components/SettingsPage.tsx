import type { Dispatch, SetStateAction } from "react";
import type { I18nKey, Locale } from "../i18n";

type ApprovalPolicy = "ask" | "auto" | "never";

type SettingsPageProps = {
  locale: Locale;
  setLocale: Dispatch<SetStateAction<Locale>>;
  approvalPolicy: ApprovalPolicy;
  setApprovalPolicy: Dispatch<SetStateAction<ApprovalPolicy>>;
  t: (key: I18nKey) => string;
  onBack: () => void;
};

export function SettingsPage({ locale, setLocale, approvalPolicy, setApprovalPolicy, t, onBack }: SettingsPageProps) {
  const saveSettings = async () => {
    await window.codecub.saveSettings({ language: locale, approvalPolicy });
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
        <label>
          {t("apiKeySource")}
          <input value={t("environment")} readOnly />
        </label>
        <button className="button primary" type="button" onClick={saveSettings}>
          {t("save")}
        </button>
      </main>
    </div>
  );
}
