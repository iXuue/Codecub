import { useState } from "react";
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
