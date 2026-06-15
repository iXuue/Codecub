import { useEffect, useState } from "react";
import type { I18nKey } from "../i18n";
import type { ChatState } from "../state/chatState";

type ChatViewProps = {
  t: (key: I18nKey) => string;
  chatState: ChatState;
  onSend: (message: string) => void;
  onStop: () => void;
};

export function ChatView({ t, chatState, onSend, onStop }: ChatViewProps) {
  const [draft, setDraft] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!chatState.isRunning) {
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [chatState.isRunning]);

  const status = chatState.runStatus;
  const startedAtMs = status?.startedAt ? new Date(status.startedAt).getTime() : Number.NaN;
  const elapsedMs = Number.isFinite(startedAtMs) ? now - startedAtMs : status?.elapsedMs ?? 0;

  function submit() {
    const message = draft.trim();
    if (!message || chatState.isRunning) {
      return;
    }
    setDraft("");
    onSend(message);
  }

  return (
    <section className="chat-view" aria-label="Chat">
      {chatState.isRunning && status ? (
        <div className="run-status-strip" aria-label={t("activeRunStatus")}>
          <span className="run-status-dot" />
          <span className="run-status-label">{status.label || t("running")}</span>
          {status.detail ? <span className="run-status-detail">{status.detail}</span> : null}
          <span className="run-status-elapsed">{formatElapsed(elapsedMs)}</span>
        </div>
      ) : null}
      <div className="message-list">
        {chatState.messages.length === 0 ? (
          <div className="empty-state">{t("emptyChat")}</div>
        ) : (
          chatState.messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-role">{message.role}</div>
              <div className="message-content">{message.content}</div>
            </article>
          ))
        )}
      </div>
      <div className="composer">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              submit();
            }
          }}
          placeholder={t("inputPlaceholder")}
          rows={2}
        />
        <button className="button primary" type="button" onClick={submit} disabled={chatState.isRunning || !draft.trim()}>
          {t("send")}
        </button>
        <button className="button secondary" type="button" onClick={onStop} disabled={!chatState.isRunning}>
          {t("stop")}
        </button>
      </div>
    </section>
  );
}

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
