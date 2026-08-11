import type { I18nKey } from "../i18n";

type ToolbarProps = {
  t: (key: I18nKey) => string;
  projectPath?: string;
  title?: string;
  meta?: string;
  onSettings?: () => void;
  onBack?: () => void;
  backLabel?: string;
};

export function Toolbar({ t, projectPath, title, meta, onSettings, onBack, backLabel }: ToolbarProps) {
  const subtitle = meta ?? projectPath ?? t("backendNotStarted");
  return (
    <header className="toolbar">
      <div className="brand">
        <div className="brand-mark">C</div>
        <div>
          <div className="brand-name">{title ?? t("appName")}</div>
          <div className="brand-meta">{subtitle}</div>
        </div>
      </div>
      <div className="toolbar-actions">
        {onBack ? (
          <button className="button secondary toolbar-pill" type="button" onClick={onBack}>
            {backLabel ?? t("back")}
          </button>
        ) : null}
        {onSettings ? (
          <button className="button secondary toolbar-pill" type="button" onClick={onSettings}>
            {t("settings")}
          </button>
        ) : null}
        <div className="window-controls" aria-label={t("windowControls")}>
          <button className="window-control" type="button" aria-label={t("minimizeWindow")} onClick={() => window.codecub.minimizeWindow()}>
            -
          </button>
          <button
            className="window-control"
            type="button"
            aria-label={t("maximizeWindow")}
            onClick={() => window.codecub.toggleMaximizeWindow()}
          >
            □
          </button>
          <button className="window-control close" type="button" aria-label={t("closeWindow")} onClick={() => window.codecub.closeWindow()}>
            ×
          </button>
        </div>
      </div>
    </header>
  );
}
