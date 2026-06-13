import type { I18nKey } from "../i18n";
import type { RecentProject } from "../state/sessionIndex";
import { Toolbar } from "./Toolbar";

type WelcomePageProps = {
  t: (key: I18nKey) => string;
  recentProjects: RecentProject[];
  onOpenProject: () => void;
  onSettings: () => void;
};

export function WelcomePage({ t, recentProjects, onOpenProject, onSettings }: WelcomePageProps) {
  return (
    <div className="app-shell">
      <Toolbar t={t} onSettings={onSettings} />
      <main className="welcome">
        <section className="welcome-primary">
          <div className="large-mark">C</div>
          <h1>{t("appName")}</h1>
          <button className="button primary" type="button" onClick={onOpenProject}>
            {t("openProject")}
          </button>
        </section>
        <section className="recent-panel" aria-label={t("recentProjects")}>
          <div className="section-title">{t("recentProjects")}</div>
          {recentProjects.length === 0 ? (
            <div className="empty-state">{t("noRecentProjects")}</div>
          ) : (
            <div className="recent-list">
              {recentProjects.map((project) => (
                <div className="recent-item" key={project.path}>
                  <div className="recent-name">{project.name}</div>
                  <div className="recent-path">{project.path}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
