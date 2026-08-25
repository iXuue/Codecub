import { useEffect, useMemo, useState } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";
import type { ChatState } from "../state/chatState";

type ChatActivityStreamProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
  chatState: ChatState;
};

type ActivityTone = "active" | "done" | "error" | "muted";

type ActivityEntry = {
  id: string;
  tone: ActivityTone;
  title: string;
  detail: string;
  code: string;
};

type ActivityView = {
  elapsed: string;
  currentTitle: string;
  entries: ActivityEntry[];
};

const activityEventTypes = new Set([
  "user_message_received",
  "run_status",
  "assistant_delta",
  "assistant_message",
  "tool_result",
  "approval_requested",
  "approval_resolved",
  "diff_summary",
  "run_completed",
  "run_failed",
  "run_canceled",
]);
const STALLED_STATUS_MS = 60_000;

export function ChatActivityStream({ t, events, chatState }: ChatActivityStreamProps) {
  const [now, setNow] = useState(() => Date.now());
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (!chatState.isRunning) {
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [chatState.isRunning]);

  const view = useMemo(() => buildActivityView(t, events, chatState, now), [t, events, chatState, now]);
  if (!view || view.entries.length === 0) {
    return null;
  }

  return (
    <section className="chat-activity-stream" aria-label={t("activityStream")}>
      <button
        className="activity-summary"
        type="button"
        aria-expanded={expanded}
        aria-label={`${t("activityProcessed")} ${view.elapsed}: ${view.currentTitle}`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span>{t("activityProcessed")}</span>
        <strong>{view.elapsed}</strong>
        <span className="activity-summary-current">{view.currentTitle}</span>
        <span className="activity-chevron" aria-hidden="true">{expanded ? "v" : ">"}</span>
      </button>
      {expanded ? (
        <div className="activity-entries">
          {view.entries.map((entry) => (
            <article className={`activity-entry ${entry.tone}`} key={entry.id}>
              <span className="activity-entry-icon" aria-hidden="true" />
              <div className="activity-entry-body">
                <div className="activity-entry-title">{entry.title}</div>
                {entry.detail ? <div className="activity-entry-detail">{entry.detail}</div> : null}
                {entry.code ? <code className="activity-entry-code">{entry.code}</code> : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function buildActivityView(
  t: (key: I18nKey) => string,
  events: BackendEvent[],
  chatState: ChatState,
  now: number,
): ActivityView | null {
  const runId = currentRunId(chatState, events);
  if (!runId) {
    return null;
  }

  const runEvents = events.filter((event) => event.run_id === runId && activityEventTypes.has(event.type));
  if (runEvents.length === 0 && !chatState.isRunning) {
    return null;
  }

  const entries: ActivityEntry[] = [];
  const latestStatus = latestEvent(runEvents, "run_status");
  const firstTime = firstTimestamp(runEvents, chatState.runStatus?.startedAt);
  const lastTime = lastTimestamp(runEvents);
  const elapsedMs = chatState.isRunning ? now - firstTime : Math.max(lastTime - firstTime, chatState.runStatus?.elapsedMs ?? 0);

  const userMessage = runEvents.find((event) => event.type === "user_message_received");
  if (userMessage) {
    entries.push({
      id: `${userMessage.timestamp}:user`,
      tone: "done",
      title: t("activityTaskReceived"),
      detail: clip(stringValue(userMessage.payload.message), 96),
      code: "",
    });
  }

  for (const event of runEvents) {
    if (event.type === "tool_result") {
      const toolName = stringValue(event.payload.tool_name) || t("toolName");
      entries.push({
        id: `${event.timestamp}:tool:${entries.length}`,
        tone: event.payload.error_type ? "error" : "done",
        title: `${t("activityCommandRan")} ${toolName}`,
        detail: toolStatus(t, event),
        code: clip(commandText(event.payload), 120),
      });
    }
    if (event.type === "diff_summary") {
      entries.push({
        id: `${event.timestamp}:diff:${entries.length}`,
        tone: "done",
        title: `${t("activityFilesEdited")} ${diffCount(event.payload)} ${t("activityFileCount")}`,
        detail: t("activityCheckingChanges"),
        code: "",
      });
    }
    if (event.type === "approval_requested") {
      entries.push({
        id: `${event.timestamp}:approval:${entries.length}`,
        tone: "active",
        title: t("activityAwaitingApproval"),
        detail: clip(stringValue(event.payload.summary), 96),
        code: stringValue(event.payload.tool_name),
      });
    }
    if (event.type === "approval_resolved") {
      entries.push({
        id: `${event.timestamp}:approval-resolved:${entries.length}`,
        tone: "done",
        title: t("activityApprovalResolved"),
        detail: stringValue(event.payload.decision),
        code: "",
      });
    }
    if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_canceled") {
      entries.push(finalEntry(t, event, entries.length));
    }
  }

  if (chatState.isRunning) {
    entries.push(activeEntry(t, latestStatus, runEvents, entries.length));
  }

  const visibleEntries = collapseDuplicateActivity(entries).slice(-8);

  return {
    elapsed: formatElapsed(elapsedMs),
    currentTitle: visibleEntries.at(-1)?.title || t("activityWorking"),
    entries: visibleEntries,
  };
}

function currentRunId(chatState: ChatState, events: BackendEvent[]): string {
  if (chatState.activeRunId) {
    return chatState.activeRunId;
  }
  if (chatState.runStatus?.runId) {
    return chatState.runStatus.runId;
  }
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (activityEventTypes.has(events[index].type) && events[index].run_id) {
      return events[index].run_id;
    }
  }
  return "";
}

function activeEntry(
  t: (key: I18nKey) => string,
  status: BackendEvent | undefined,
  events: BackendEvent[],
  index: number,
): ActivityEntry {
  const phase = stringValue(status?.payload.phase).toLowerCase();
  const hasAssistantDelta = events.some((event) => event.type === "assistant_delta");
  const hasToolResult = events.some((event) => event.type === "tool_result");

  return {
    id: `active:${index}`,
    tone: "active",
    title: activeTitle(t, phase, hasAssistantDelta, hasToolResult),
    detail: activeDetail(t, status),
    code: "",
  };
}

function activeTitle(
  t: (key: I18nKey) => string,
  phase: string,
  hasAssistantDelta: boolean,
  hasToolResult: boolean,
): string {
  if (phase.includes("received")) {
    return t("activityTaskReceived");
  }
  if (phase.includes("checking_workspace")) {
    return t("activityCheckingWorkspace");
  }
  if (phase.includes("loading_memory")) {
    return t("activityLoadingMemory");
  }
  if (phase.includes("building_prompt")) {
    return t("activityBuildingPrompt");
  }
  if (phase.includes("building_context") || phase.includes("context")) {
    return t("activityBuildingContext");
  }
  if (phase.includes("model_request")) {
    return t("activityRequestingModel");
  }
  if (phase.includes("tool")) {
    return t("activityRunningTool");
  }
  if (phase.includes("diff") || phase.includes("change")) {
    return t("activityCheckingChanges");
  }
  if (phase.includes("finalization_required")) {
    return t("activityGeneratingEvidenceAnswer");
  }
  if (phase.includes("model") || phase.includes("final") || hasAssistantDelta) {
    return t("activityReceivingResponse");
  }
  if (!phase && hasToolResult) {
    return t("activityCheckingChanges");
  }
  return t("activityWorking");
}

function activeDetail(t: (key: I18nKey) => string, status: BackendEvent | undefined): string {
  const payload = status?.payload ?? {};
  const silentForMs = Number(payload.silent_for_ms ?? 0);
  if (payload.heartbeat === true && Number.isFinite(silentForMs) && silentForMs >= STALLED_STATUS_MS) {
    return t("activityNoRecentProgress");
  }
  return clip(stringValue(payload.detail), 96);
}

function finalEntry(t: (key: I18nKey) => string, event: BackendEvent, index: number): ActivityEntry {
  if (event.type === "run_failed") {
    return {
      id: `${event.timestamp}:final:${index}`,
      tone: "error",
      title: t("activityFailed"),
      detail: clip(stringValue(event.payload.message || event.payload.error_type), 120),
      code: "",
    };
  }
  if (event.type === "run_canceled") {
    return {
      id: `${event.timestamp}:final:${index}`,
      tone: "muted",
      title: t("activityCanceled"),
      detail: stringValue(event.payload.reason),
      code: "",
    };
  }
  return {
    id: `${event.timestamp}:final:${index}`,
    tone: "done",
    title: t("activityCompleted"),
    detail: clip(stringValue(event.payload.final), 120),
    code: "",
  };
}

function collapseDuplicateActivity(entries: ActivityEntry[]): ActivityEntry[] {
  const collapsed: ActivityEntry[] = [];
  for (const entry of entries) {
    const previous = collapsed[collapsed.length - 1];
    if (previous && previous.title === entry.title && previous.detail === entry.detail && previous.code === entry.code) {
      collapsed[collapsed.length - 1] = entry;
    } else {
      collapsed.push(entry);
    }
  }
  return collapsed;
}

function latestEvent(events: BackendEvent[], type: string): BackendEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === type) {
      return events[index];
    }
  }
  return undefined;
}

function firstTimestamp(events: BackendEvent[], fallback: string | undefined): number {
  const first = fallback || events[0]?.timestamp || "";
  const value = new Date(first).getTime();
  return Number.isFinite(value) ? value : Date.now();
}

function lastTimestamp(events: BackendEvent[]): number {
  const last = events[events.length - 1]?.timestamp || "";
  const value = new Date(last).getTime();
  return Number.isFinite(value) ? value : Date.now();
}

function toolStatus(t: (key: I18nKey) => string, event: BackendEvent): string {
  const status = stringValue(event.payload.status || event.payload.error_type);
  const result = stringValue(event.payload.result);
  if (status && result) return `${status}: ${result}`;
  return status || result || (event.payload.workspace_changed ? t("workspaceChanged") : "");
}

function commandText(payload: Record<string, unknown>): string {
  return stringValue(payload.command || payload.cmd || formatToolArgs(payload.args) || payload.output || payload.message);
}

function formatToolArgs(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function diffCount(payload: Record<string, unknown>): number {
  const summary = Array.isArray(payload.diff_summary) ? payload.diff_summary : [];
  return summary.length;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : value === null || value === undefined ? "" : String(value);
}

function clip(value: string, limit: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > limit ? `${compact.slice(0, limit - 3)}...` : compact;
}

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
