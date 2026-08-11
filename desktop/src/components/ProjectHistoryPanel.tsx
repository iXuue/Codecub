import type { ProjectSessionSummary } from "../../electron/ipcTypes";
import type { I18nKey } from "../i18n";

const EMPTY_CHAT_TITLE = "__codecub_empty_chat__";

type ProjectHistoryPanelProps = {
  t: (key: I18nKey) => string;
  sessions: ProjectSessionSummary[];
  activeSessionId: string;
  error: string;
  onRefresh: () => void;
  onResume: (sessionId: string) => void;
  onCreate: () => void;
  onDelete: (sessionId: string) => void;
};

export function ProjectHistoryPanel({
  t,
  sessions,
  activeSessionId,
  error,
  onRefresh,
  onResume,
  onCreate,
  onDelete,
}: ProjectHistoryPanelProps) {
  return (
    <section className="side-panel" aria-label={t("chatHistory")}>
      <div className="side-panel-header">
        <span>{t("chatHistory")}</span>
        <div className="side-panel-actions">
          <button className="button mini" type="button" onClick={onCreate}>
            {t("newChat")}
          </button>
          <button className="button mini" type="button" onClick={onRefresh}>
            {t("refresh")}
          </button>
        </div>
      </div>
      {error ? <div className="side-error">{error}</div> : null}
      {sessions.length === 0 ? (
        <div className="empty-state compact">{t("noProjectSessions")}</div>
      ) : (
        <div className="side-list">
          {sessions.map((session) => {
            const selected = session.id === activeSessionId;
            const isEmptyManualChat = session.title === EMPTY_CHAT_TITLE && session.messageCount === 0;
            const title = isEmptyManualChat ? t("untitledChat") : session.title || t("untitledChat");
            return (
              <div className={selected ? "side-list-row active" : "side-list-row"} key={session.id}>
                <button className="side-list-item" type="button" onClick={() => onResume(session.id)}>
                  <span className="side-list-title">{title}</span>
                  <span className="side-list-preview">{session.preview || (isEmptyManualChat ? t("emptyChatReady") : t("noMessagesYet"))}</span>
                  <span className="side-list-meta">
                    {formatSessionTime(session.updatedAt)} - {session.messageCount} {t("messages")}
                  </span>
                </button>
                <button className="icon-button danger" type="button" aria-label={`${t("deleteChat")} ${title}`} onClick={() => onDelete(session.id)}>
                  x
                </button>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function formatSessionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString([], {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
