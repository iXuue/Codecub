import type { I18nKey } from "../i18n";
import type { ChatState } from "../state/chatState";

type CubStatusChipProps = {
  t: (key: I18nKey) => string;
  chatState: ChatState;
};

export function CubStatusChip({ t, chatState }: CubStatusChipProps) {
  const status = chatState.runStatus;
  const label = status?.label || (chatState.isRunning ? t("running") : t("ready"));
  const elapsedMs = status?.elapsedMs ?? 0;

  return (
    <section className={chatState.isRunning ? "cub-status-chip running" : "cub-status-chip"} aria-label={t("cubStatus")}>
      <span className="cub-status-mark">C</span>
      <span className="cub-status-main">
        <span className="cub-status-label">{label}</span>
        {status?.detail ? <span className="cub-status-detail">{status.detail}</span> : null}
      </span>
      {chatState.isRunning ? <span className="cub-status-time">{formatClock(elapsedMs)}</span> : null}
    </section>
  );
}

function formatClock(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
