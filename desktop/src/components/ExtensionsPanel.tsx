import type { ProjectExtensions } from "../../electron/ipcTypes";
import type { I18nKey } from "../i18n";

type ExtensionsPanelProps = {
  t: (key: I18nKey) => string;
  extensions: ProjectExtensions;
  error: string;
  onRefresh: () => void;
  onInstallSkill: () => void;
  onInstallPlugin: () => void;
};

export function ExtensionsPanel({
  t,
  extensions,
  error,
  onRefresh,
  onInstallSkill,
  onInstallPlugin,
}: ExtensionsPanelProps) {
  return (
    <section className="side-panel" aria-label={t("pluginsAndSkills")}>
      <div className="side-panel-header">
        <span>{t("pluginsAndSkills")}</span>
        <button className="button mini" type="button" onClick={onRefresh}>
          {t("refresh")}
        </button>
      </div>
      <div className="extension-actions">
        <button className="button secondary" type="button" onClick={onInstallSkill}>
          {t("installSkill")}
        </button>
        <button className="button secondary" type="button" onClick={onInstallPlugin}>
          {t("installPlugin")}
        </button>
      </div>
      {error ? <div className="side-error">{error}</div> : null}
      <ExtensionList title={t("skills")} items={extensions.skills} emptyText={t("noSkills")} />
      <ExtensionList title={t("plugins")} items={extensions.plugins} emptyText={t("noPlugins")} />
    </section>
  );
}

function ExtensionList({
  title,
  items,
  emptyText,
}: {
  title: string;
  items: ProjectExtensions["skills"];
  emptyText: string;
}) {
  return (
    <div className="extension-group">
      <div className="extension-group-title">{title}</div>
      {items.length === 0 ? (
        <div className="empty-state compact">{emptyText}</div>
      ) : (
        <div className="side-list">
          {items.map((item) => (
            <div className="side-list-static" key={`${item.kind}:${item.id}`}>
              <span className="side-list-title">{item.name}</span>
              <span className="side-list-meta">{item.id}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
