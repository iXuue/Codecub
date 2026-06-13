import { useState } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";

type RunLogSidebarProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
};

export function RunLogSidebar({ t, events }: RunLogSidebarProps) {
  const [debugMode, setDebugMode] = useState(false);

  return (
    <aside className="run-log" aria-label={t("runLog")}>
      <div className="run-log-header">
        <span>{t("runLog")}</span>
        <label className="debug-toggle">
          <input type="checkbox" checked={debugMode} onChange={(event) => setDebugMode(event.target.checked)} />
          {t("debugMode")}
        </label>
      </div>
      <div className="event-list">
        {events.length === 0 ? (
          <div className="empty-state compact">{t("backendNotStarted")}</div>
        ) : (
          events.map((event, index) => (
            <div className="event-item" key={`${event.timestamp}:${event.type}:${index}`}>
              <div className="event-type">{eventLabel(event)}</div>
              <div className="event-meta">{event.timestamp}</div>
              <div className="event-meta">{event.run_id || event.session_id}</div>
              {debugMode ? <DebugPayload t={t} event={event} /> : <CompactPayload event={event} />}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

function eventLabel(event: BackendEvent): string {
  if (event.type === "approval_requested") {
    return `approval: ${String(event.payload.tool_name ?? "")}`;
  }
  if (event.type === "approval_resolved") {
    return `approval ${String(event.payload.decision ?? "")}`;
  }
  if (event.type === "tool_result") {
    return `tool: ${String(event.payload.tool_name ?? "")}`;
  }
  if (event.type === "diff_summary") {
    return "diff_summary";
  }
  return event.type;
}

function CompactPayload({ event }: { event: BackendEvent }) {
  const message =
    event.payload.message ??
    event.payload.error_type ??
    event.payload.status ??
    event.payload.decision ??
    event.payload.final ??
    event.payload.imported_count ??
    "";
  return message ? <div className="event-summary">{String(message)}</div> : null;
}

function DebugPayload({ t, event }: { t: (key: I18nKey) => string; event: BackendEvent }) {
  const payload = sanitize(event.payload) as Record<string, unknown>;
  return (
    <div className="debug-payload">
      {payload.trace_path ? <div className="event-meta">{t("tracePath")}: {String(payload.trace_path)}</div> : null}
      {payload.report_path ? <div className="event-meta">{t("reportPath")}: {String(payload.report_path)}</div> : null}
      <div className="event-meta">{t("payload")}</div>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </div>
  );
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
