import { useEffect, useRef, useState, type MouseEvent } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ChatState } from "../state/chatState";
import { gsap, motionAllowed, useGSAP } from "../motion/gsapSetup";
import { ChatActivityStream } from "./ChatActivityStream";

type ChatViewProps = {
  t: (key: I18nKey) => string;
  chatState: ChatState;
  events: BackendEvent[];
  activeSessionId?: string;
  onSend: (message: string) => void;
  onStop: () => void;
};

export function ChatView({ t, chatState, events, activeSessionId = "", onSend, onStop }: ChatViewProps) {
  const chatRef = useRef<HTMLElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [draft, setDraft] = useState("");

  useGSAP(
    () => {
      if (!motionAllowed() || chatState.messages.length === 0) {
        return;
      }
      gsap.from(".message:last-child", {
        autoAlpha: 0,
        y: 10,
        duration: 0.22,
        ease: "power2.out",
      });
    },
    { dependencies: [chatState.messages.length], scope: chatRef, revertOnUpdate: true },
  );

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) {
      return;
    }
    const scroll = () => {
      list.scrollTop = list.scrollHeight;
    };
    const frame = window.requestAnimationFrame(scroll);
    return () => window.cancelAnimationFrame(frame);
  }, [chatState.messages.length, events.length, chatState.isRunning, chatState.runStatus?.phase, chatState.runStatus?.updatedAt]);

  function submit() {
    const message = draft.trim();
    if (!message || chatState.isRunning) {
      return;
    }
    setDraft("");
    onSend(message);
  }

  function focusComposer(event: MouseEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.closest("textarea, button")) {
      return;
    }
    event.preventDefault();
    textareaRef.current?.focus();
  }

  return (
    <section className="chat-view" aria-label="Chat" ref={chatRef}>
      <div className="message-list" ref={messageListRef}>
        {chatState.messages.length === 0 ? (
          <div className="chat-empty-state">
            <div className="chat-empty-mark">C</div>
            <div>
              <div className="chat-empty-title">{activeSessionId ? t("emptyActiveChatTitle") : t("emptyChatTitle")}</div>
              <div className="chat-empty-subtitle">{activeSessionId ? t("emptyActiveChat") : t("emptyChat")}</div>
            </div>
          </div>
        ) : (
          chatState.messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-role">{message.role}</div>
              <div className="message-content">{message.content}</div>
            </article>
          ))
        )}
        <ChatActivityStream t={t} events={events} chatState={chatState} />
      </div>
      <div className="composer" onMouseDown={focusComposer}>
        <textarea
          ref={textareaRef}
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
