import type { I18nKey } from "../i18n";
import type { RecentProject } from "../state/sessionIndex";
import { Toolbar } from "./Toolbar";

type WelcomePageProps = {
  t: (key: I18nKey) => string;
  recentProjects: RecentProject[];
  onOpenProject: () => void;
  onOpenRecentProject: (projectPath: string) => void;
  onSettings: () => void;
};

export function WelcomePage({ t, recentProjects, onOpenProject, onOpenRecentProject, onSettings }: WelcomePageProps) {
  const latestProject = recentProjects[0];
  const primaryAction = latestProject ? () => onOpenRecentProject(latestProject.path) : onOpenProject;
  return (
    <div className="app-shell">
      <Toolbar t={t} onSettings={onSettings} />
      <main className="welcome command-deck">
        <section className="welcome-primary">
          <div className="welcome-mark-row">
            <div className="large-mark">C</div>
            <div className="welcome-kicker">{t("welcomeKicker")}</div>
          </div>
          <div className="welcome-copy">
            <h1>{t("appName")}</h1>
            <p>{t("welcomeSubtitle")}</p>
          </div>
          <div className="welcome-actions">
            <button className="button primary command-button" type="button" onClick={primaryAction}>
              <span>{latestProject ? t("continueLatestProject") : t("openProject")}</span>
              <span className="button-arrow">-&gt;</span>
            </button>
            {latestProject ? (
              <button className="button secondary command-secondary" type="button" onClick={onOpenProject}>
                {t("openOtherProject")}
              </button>
            ) : null}
            <div className="welcome-hint">
              {latestProject ? `${t("latestProjectHint")} ${latestProject.name}` : t("openProjectHint")}
            </div>
          </div>
          <div className="welcome-metrics" aria-label={t("welcomeMetrics")}>
            <div>
              <strong>{recentProjects.length}</strong>
              <span>{t("recentProjects")}</span>
            </div>
            <div>
              <strong>{t("ready")}</strong>
              <span>{t("agentRuntime")}</span>
            </div>
          </div>
        </section>
        <section className="recent-panel" aria-label={t("recentProjects")}>
          <div className="recent-panel-header">
            <div>
              <div className="section-eyebrow">{t("commandDeck")}</div>
              <div className="section-title">{t("recentProjects")}</div>
            </div>
            <button className="button secondary mini" type="button" onClick={onOpenProject}>
              {t("openProject")}
            </button>
          </div>
          {recentProjects.length === 0 ? (
            <div className="welcome-empty-state">
              <div className="empty-state-title">{t("noRecentProjects")}</div>
              <div className="empty-state">{t("openProjectHint")}</div>
            </div>
          ) : (
            <div className="recent-list">
              {recentProjects.map((project, index) => (
                <button className="recent-item" key={project.path} type="button" onClick={() => onOpenRecentProject(project.path)}>
                  <span className="recent-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="recent-main">
                    <span className="recent-name">{project.name}</span>
                    <span className="recent-path">{project.path}</span>
                  </span>
                  <span className="recent-open">{t("launchProject")}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
