import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";

type DiffPreviewPanelProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
};

export function DiffPreviewPanel({ t, events }: DiffPreviewPanelProps) {
  const diffEvents = events
    .filter((event) => event.type === "diff_summary" || event.type === "tool_result")
    .filter((event) => event.payload.workspace_changed || Array.isArray(event.payload.diff_summary))
    .slice(-5)
    .reverse();

  return (
    <section className="diff-panel" aria-label={t("diffPreview")}>
      <div className="panel-title">{t("diffPreview")}</div>
      {diffEvents.length === 0 ? (
        <div className="empty-state compact">{t("noDiff")}</div>
      ) : (
        diffEvents.map((event, index) => (
          <div className="diff-item" key={`${event.timestamp}:${event.type}:${index}`}>
            <div className="diff-title">
              <span>{String(event.payload.tool_name ?? event.type)}</span>
              <span>{event.payload.workspace_changed ? "changed" : "no-change"}</span>
            </div>
            <PathList paths={event.payload.affected_paths} />
            <SummaryList summary={event.payload.diff_summary} />
          </div>
        ))
      )}
    </section>
  );
}

function PathList({ paths }: { paths: unknown }) {
  const items = Array.isArray(paths) ? paths.map(String) : [];
  if (items.length === 0) {
    return null;
  }
  return (
    <ul className="path-list">
      {items.map((path) => (
        <li key={path}>{path}</li>
      ))}
    </ul>
  );
}

function SummaryList({ summary }: { summary: unknown }) {
  const items = Array.isArray(summary) ? summary : [];
  if (items.length === 0) {
    return null;
  }
  return (
    <pre className="diff-summary">
      {JSON.stringify(items, null, 2)}
    </pre>
  );
}
