import type { ProjectSessionSummary } from "../../electron/ipcTypes";
import type { I18nKey } from "../i18n";

type ProjectHistoryPanelProps = {
  t: (key: I18nKey) => string;
  sessions: ProjectSessionSummary[];
  error: string;
  onRefresh: () => void;
  onResume: (sessionId: string) => void;
};

export function ProjectHistoryPanel({ t, sessions, error, onRefresh, onResume }: ProjectHistoryPanelProps) {
  return (
    <section className="side-panel" aria-label={t("chatHistory")}>
      <div className="side-panel-header">
        <span>{t("chatHistory")}</span>
        <button className="button mini" type="button" onClick={onRefresh}>
          {t("refresh")}
        </button>
      </div>
      {error ? <div className="side-error">{error}</div> : null}
      {sessions.length === 0 ? (
        <div className="empty-state compact">{t("noProjectSessions")}</div>
      ) : (
        <div className="side-list">
          {sessions.map((session) => (
            <button className="side-list-item" type="button" key={session.id} onClick={() => onResume(session.id)}>
              <span className="side-list-title">{session.preview || session.id}</span>
              <span className="side-list-meta">
                {session.id} · {session.messageCount} {t("messages")}
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
