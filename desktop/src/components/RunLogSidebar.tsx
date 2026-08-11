import { useMemo, useState } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";

type RunLogSidebarProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
};

type EventField = {
  label: string;
  value: string;
};

type EventView = {
  title: string;
  detail: string;
  result: string;
  fields: EventField[];
};

const noisyDefaultEvents = new Set(["assistant_delta"]);
const STALLED_STATUS_MS = 60_000;

export function RunLogSidebar({ t, events }: RunLogSidebarProps) {
  const [debugMode, setDebugMode] = useState(false);
  const visibleEvents = useMemo(
    () => (debugMode ? events : compactReadableEvents(events)),
    [debugMode, events],
  );

  return (
    <aside className="run-log" aria-label={t("runLog")}>
      <div className="run-log-header">
        <div>
          <span>{t("runLog")}</span>
          <div className="run-log-subtitle">{t("runLogReadableHint")}</div>
        </div>
        <label className="debug-toggle">
          <input type="checkbox" checked={debugMode} onChange={(event) => setDebugMode(event.target.checked)} />
          {t("debugMode")}
        </label>
      </div>
      <div className="event-list">
        {visibleEvents.length === 0 ? (
          <div className="empty-state compact">{events.length === 0 ? t("backendNotStarted") : t("runLogNoReadableEvents")}</div>
        ) : (
          visibleEvents.map((event, index) => <RunLogItem t={t} event={event} debugMode={debugMode} key={`${event.timestamp}:${event.type}:${index}`} />)
        )}
      </div>
    </aside>
  );
}

function RunLogItem({ t, event, debugMode }: { t: (key: I18nKey) => string; event: BackendEvent; debugMode: boolean }) {
  const view = describeEvent(t, event);
  return (
    <article className="event-item">
      <header className="event-card-header">
        <div>
          <div className="event-type">{view.title}</div>
          <div className="event-meta">{formatTime(event.timestamp)}</div>
        </div>
        {event.run_id ? <span className="event-chip">{shortId(event.run_id)}</span> : null}
      </header>
      {view.detail ? <div className="event-detail">{view.detail}</div> : null}
      {view.result ? (
        <div className="event-return">
          <span>{t("eventReturn")}</span>
          <strong>{view.result}</strong>
        </div>
      ) : null}
      {debugMode ? <DebugDetails t={t} event={event} fields={view.fields} /> : null}
    </article>
  );
}

function describeEvent(t: (key: I18nKey) => string, event: BackendEvent): EventView {
  const payload = event.payload;
  const fields = commonFields(t, event);

  switch (event.type) {
    case "session_started":
      fields.push(field(t("project"), compactPath(payload.cwd)), field(t("approvalPolicy"), stringValue(payload.approval_policy)));
      return {
        title: t("eventSessionStarted"),
        detail: t("eventSessionStartedDetail"),
        result: compactPath(payload.session_path) || t("eventSessionReady"),
        fields,
      };
    case "session_closed":
      return { title: t("eventSessionClosed"), detail: t("eventSessionClosedDetail"), result: "", fields };
    case "user_message_received":
      return {
        title: t("eventUserMessage"),
        detail: clip(stringValue(payload.message), 90) || t("eventUserMessageDetail"),
        result: "",
        fields,
      };
    case "run_status":
      fields.push(field(t("eventPhase"), stringValue(payload.phase)), field(t("eventDetail"), stringValue(payload.detail)));
      return {
        title: t("eventRunStatus"),
        detail: runStatusText(t, payload),
        result: elapsedText(t, payload.elapsed_ms),
        fields,
      };
    case "assistant_message":
      return {
        title: t("eventAssistantMessage"),
        detail: t("eventAssistantMessageDetail"),
        result: clip(stringValue(payload.text ?? payload.final), 110),
        fields,
      };
    case "assistant_delta":
      return {
        title: t("eventAssistantDelta"),
        detail: t("eventAssistantDeltaDetail"),
        result: clip(stringValue(payload.text), 80),
        fields,
      };
    case "tool_started":
      fields.push(field(t("toolName"), stringValue(payload.tool_name)));
      return {
        title: t("activityRunningTool"),
        detail: toolStartedDetail(t, payload),
        result: stringValue(payload.tool_name),
        fields,
      };
    case "tool_result":
      fields.push(field(t("toolName"), stringValue(payload.tool_name)));
      return {
        title: t("eventToolResult"),
        detail: toolDetail(t, payload),
        result: toolResult(t, payload),
        fields,
      };
    case "diff_summary":
      return {
        title: t("eventDiffSummary"),
        detail: t("eventDiffSummaryDetail"),
        result: diffResult(t, payload),
        fields,
      };
    case "approval_requested":
      fields.push(field(t("toolName"), stringValue(payload.tool_name)), field(t("riskLevel"), stringValue(payload.risk)));
      return {
        title: t("eventApprovalRequested"),
        detail: stringValue(payload.summary) || t("eventApprovalRequestedDetail"),
        result: stringValue(payload.tool_name),
        fields,
      };
    case "approval_resolved":
      return {
        title: t("eventApprovalResolved"),
        detail: t("eventApprovalResolvedDetail"),
        result: stringValue(payload.decision),
        fields,
      };
    case "run_completed":
      return {
        title: t("eventRunCompleted"),
        detail: t("eventRunCompletedDetail"),
        result: clip(stringValue(payload.final), 110),
        fields,
      };
    case "run_failed":
      return {
        title: t("eventRunFailed"),
        detail: stringValue(payload.error_type) || t("eventRunFailedDetail"),
        result: clip(stringValue(payload.message), 110),
        fields,
      };
    case "run_canceled":
      return {
        title: t("eventRunCanceled"),
        detail: t("eventRunCanceledDetail"),
        result: stringValue(payload.reason),
        fields,
      };
    case "legacy_import_detected":
      return {
        title: t("eventLegacyDetected"),
        detail: t("eventLegacyDetectedDetail"),
        result: countText(t, payload.session_count),
        fields,
      };
    case "legacy_import_completed":
      return {
        title: t("eventLegacyCompleted"),
        detail: t("eventLegacyCompletedDetail"),
        result: countText(t, payload.imported_count),
        fields,
      };
    case "legacy_import_failed":
      return {
        title: t("eventLegacyFailed"),
        detail: t("eventLegacyFailedDetail"),
        result: clip(stringValue(payload.message), 110),
        fields,
      };
    default:
      return {
        title: t("eventUnknown"),
        detail: event.type,
        result: "",
        fields,
      };
  }
}

function compactReadableEvents(events: BackendEvent[]): BackendEvent[] {
  const visible: BackendEvent[] = [];
  for (const event of events) {
    if (noisyDefaultEvents.has(event.type) || isHeartbeatStatus(event)) {
      continue;
    }
    const previous = visible[visible.length - 1];
    if (previous && isDuplicateRunStatus(previous, event)) {
      visible[visible.length - 1] = event;
    } else {
      visible.push(event);
    }
  }
  return visible;
}

function isHeartbeatStatus(event: BackendEvent): boolean {
  return event.type === "run_status" && event.payload.heartbeat === true;
}

function isDuplicateRunStatus(left: BackendEvent, right: BackendEvent): boolean {
  if (left.type !== "run_status" || right.type !== "run_status") {
    return false;
  }
  return (
    stringValue(left.payload.phase) === stringValue(right.payload.phase) &&
    stringValue(left.payload.label) === stringValue(right.payload.label) &&
    stringValue(left.payload.detail) === stringValue(right.payload.detail)
  );
}

function runStatusText(t: (key: I18nKey) => string, payload: Record<string, unknown>): string {
  const label = phaseText(t, stringValue(payload.phase)) || stringValue(payload.label) || t("eventRunStatusDetail");
  const silentForMs = Number(payload.silent_for_ms ?? 0);
  if (payload.heartbeat === true && Number.isFinite(silentForMs) && silentForMs >= STALLED_STATUS_MS) {
    return `${label}: ${t("activityNoRecentProgress")}`;
  }
  return label;
}

function phaseText(t: (key: I18nKey) => string, phaseValue: string): string {
  const phase = phaseValue.toLowerCase();
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
  if (phase.includes("model") || phase.includes("final")) {
    return t("activityReceivingResponse");
  }
  if (phase.includes("tool")) {
    return t("activityRunningTool");
  }
  if (phase.includes("diff") || phase.includes("change")) {
    return t("activityCheckingChanges");
  }
  if (phase.includes("completed")) {
    return t("activityCompleted");
  }
  if (phase.includes("canceled")) {
    return t("activityCanceled");
  }
  if (phase.includes("failed")) {
    return t("activityFailed");
  }
  return "";
}

function DebugDetails({ t, event, fields }: { t: (key: I18nKey) => string; event: BackendEvent; fields: EventField[] }) {
  const payload = sanitize(event.payload) as Record<string, unknown>;
  return (
    <div className="debug-details">
      {fields.filter((item) => item.value).length > 0 ? (
        <dl className="event-fields">
          {fields
            .filter((item) => item.value)
            .map((item) => (
              <div className="event-field" key={`${item.label}:${item.value}`}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
        </dl>
      ) : null}
      <details className="debug-payload">
        <summary>{t("rawEvent")}</summary>
        <pre>{JSON.stringify(payload, null, 2)}</pre>
      </details>
    </div>
  );
}

function commonFields(t: (key: I18nKey) => string, event: BackendEvent): EventField[] {
  return [field(t("eventType"), event.type), field(t("session"), shortId(event.session_id)), field(t("run"), shortId(event.run_id))];
}

function field(label: string, value: unknown): EventField {
  return { label, value: stringValue(value) };
}

function toolDetail(t: (key: I18nKey) => string, payload: Record<string, unknown>): string {
  const toolName = stringValue(payload.tool_name);
  const status = stringValue(payload.status || payload.error_type);
  if (toolName && status) {
    return `${toolName} - ${status}`;
  }
  return toolName || t("eventToolResultDetail");
}

function toolStartedDetail(t: (key: I18nKey) => string, payload: Record<string, unknown>): string {
  const readable = stringValue(payload.title || payload.detail || payload.summary);
  if (readable) {
    return clip(readable, 110);
  }
  return stringValue(payload.tool_name) || t("activityRunningTool");
}

function toolResult(t: (key: I18nKey) => string, payload: Record<string, unknown>): string {
  if (payload.workspace_changed === true) {
    return t("workspaceChanged");
  }
  const output = stringValue(payload.output || payload.content || payload.message);
  return clip(output, 110) || t("noWorkspaceChanged");
}

function diffResult(t: (key: I18nKey) => string, payload: Record<string, unknown>): string {
  const summary = Array.isArray(payload.diff_summary) ? payload.diff_summary : [];
  if (summary.length > 0) {
    return `${summary.length} ${t("filesChanged")}`;
  }
  return t("noDiff");
}

function elapsedText(t: (key: I18nKey) => string, value: unknown): string {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms <= 0) {
    return "";
  }
  return `${t("elapsed")} ${Math.round(ms / 100) / 10}s`;
}

function countText(t: (key: I18nKey) => string, value: unknown): string {
  const count = Number(value);
  if (!Number.isFinite(count)) {
    return "";
  }
  return `${count} ${t("messages")}`;
}

function compactPath(value: unknown): string {
  const text = stringValue(value);
  if (!text) {
    return "";
  }
  const parts = text.split(/[\\/]+/).filter(Boolean);
  return parts.slice(-2).join("/");
}

function shortId(value: string): string {
  if (!value) {
    return "";
  }
  return value.length > 12 ? `${value.slice(0, 12)}...` : value;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : value === null || value === undefined ? "" : String(value);
}

function clip(value: string, limit: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > limit ? `${compact.slice(0, limit - 3)}...` : compact;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function sanitize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sanitize);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (/api[_-]?key|token|secret|password/i.test(key)) {
      result[key] = "<redacted>";
    } else {
      result[key] = sanitize(item);
    }
  }
  return result;
}
