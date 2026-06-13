import type { I18nKey } from "../i18n";

type ToolbarProps = {
  t: (key: I18nKey) => string;
  projectPath?: string;
  onSettings: () => void;
};

export function Toolbar({ t, projectPath, onSettings }: ToolbarProps) {
  return (
    <header className="toolbar">
      <div className="brand">
        <div className="brand-mark">C</div>
        <div>
          <div className="brand-name">{t("appName")}</div>
          <div className="brand-meta">{projectPath || t("backendNotStarted")}</div>
        </div>
      </div>
      <button className="button secondary" type="button" onClick={onSettings}>
        {t("settings")}
      </button>
    </header>
  );
}
