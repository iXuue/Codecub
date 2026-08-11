import { useState } from "react";
import type { I18nKey } from "../i18n";
import type { UsageGroup, UsageState } from "../state/usageState";

type Props = { t: (key: I18nKey) => string; usageState: UsageState };

export function UsagePanel({ t, usageState }: Props) {
  const [scope, setScope] = useState<"run" | "session">("run");
  const groups = scope === "run" ? usageState.runGroups : usageState.sessionGroups;
  return (
    <section className="usage-ledger" aria-label={t("usage") }>
      <div className="usage-scope" role="group" aria-label={t("usageScope")}>
        <button className={scope === "run" ? "active" : ""} type="button" onClick={() => setScope("run")}>{t("thisRun")}</button>
        <button className={scope === "session" ? "active" : ""} type="button" onClick={() => setScope("session")}>{t("thisSession")}</button>
      </div>
      {groups.length === 0 ? <div className="empty-state compact">{t("usageEmpty")}</div> : groups.map((group) => <UsageGroupCard key={group.aggregation_key} group={group} t={t} />)}
    </section>
  );
}

function UsageGroupCard({ group, t }: { group: UsageGroup; t: (key: I18nKey) => string }) {
  const input = numberValue(group.context.total_actual_input_tokens);
  const latestInput = numberValue(group.context.latest_actual_input_tokens);
  const read = numberValue(group.cache.read_tokens);
  const write = numberValue(group.cache.write_tokens);
  const uncached = numberValue(group.cache.uncached_input_tokens);
  const totalTrack = Math.max(input ?? 0, (read ?? 0) + (write ?? 0) + (uncached ?? 0), 1);
  const unavailable = group.cache.mode === "unavailable" || group.cache.mode === "unsupported";
  const endpointVerified = group.connection.endpoint_verification_status === "verified";
  const usageVerified = group.connection.usage_schema_verification_status === "verified";
  return (
    <article className="usage-group-card">
      <header className="usage-identity">
        <div><strong>{group.connection.model || t("unknownValue")}</strong><span>{group.connection.api_operator} · {group.connection.endpoint_kind}</span></div>
        <span className={`usage-quality ${group.connection.endpoint_verification_status || "unverified"}`}>{connectionLabel(group.connection.connection_type, t)}</span>
      </header>
      <div className="usage-channel-line">{group.calculation_channels.context || t("unknownValue")}</div>
      <div className="usage-evidence" aria-label={t("usageEvidence")}>
        <span className={endpointVerified ? "verified" : "unverified"}>{t("endpointIdentity")}: {endpointVerified ? t("verified") : t("unverified")}</span>
        <span className={usageVerified ? "verified" : "unverified"}>{t("usageSchema")}: {usageVerified ? t("verified") : t("unverified")}</span>
      </div>
      <div className="usage-metrics">
        <Metric label={t("latestInput")} value={tokens(latestInput, t)} />
        <Metric label={t("peakContext")} value={`${tokens(numberValue(group.context.peak_actual_input_tokens), t)} / ${tokens(numberValue(group.context.context_window), t)}`} />
        <Metric label={t("peakContextUsage")} value={ratio(numberValue(group.context.peak_utilization_ratio), t)} />
        <Metric label={t("totalInput")} value={tokens(input, t)} />
        <Metric label={t("outputTokens")} value={tokens(numberValue(group.output.output_tokens), t)} />
        <Metric label={t("reasoningTokens")} value={tokens(numberValue(group.output.reasoning_tokens), t)} />
        <Metric label={t("requests")} value={String(group.request_count)} />
      </div>
      <div className="usage-track-block">
        <div className="usage-track-heading"><span>{t("cacheLedger")}</span><strong>{unavailable ? t("unknownValue") : ratio(numberValue(group.cache.read_ratio), t)}</strong></div>
        {unavailable ? <div className="usage-unsupported">{t("dataUnverified")}</div> : (
          <>
            <div className="usage-track" aria-label={t("cacheLedger")}>
              <span className="uncached" style={{ width: `${percent(uncached, totalTrack)}%` }} />
              <span className="cache-read" style={{ width: `${percent(read, totalTrack)}%` }} />
              <span className="cache-write" style={{ width: `${percent(write, totalTrack)}%` }} />
            </div>
            <div className="usage-legend"><span>{t("uncachedInput")} {tokens(uncached, t)}</span><span>{t("cacheRead")} {tokens(read, t)}</span><span>{t("cacheWrite")} {tokens(write, t)}</span></div>
          </>
        )}
      </div>
      <div className="usage-costs">
        <span>{t("cost")}</span>
        {group.cost.length ? group.cost.map((cost) => <strong key={`${cost.kind}:${cost.unit}:${cost.unit_kind}:${cost.source}:${cost.pricing_version}:${cost.quality}`}>{cost.kind}: {cost.amount} {cost.unit}</strong>) : <strong>{t("costUnknown")}</strong>}
      </div>
      {group.warnings.length ? <div className="usage-warning">{t("dataUnverified")}</div> : null}
    </article>
  );
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="usage-metric"><span>{label}</span><strong>{value}</strong></div>; }
function connectionLabel(type: string | undefined, t: (key: I18nKey) => string): string { if (type === "relay") return t("connectionRelay"); if (type === "local") return t("connectionLocal"); if (type === "custom") return t("connectionCustom"); return t("connectionDirect"); }
function numberValue(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function tokens(value: number | null, t: (key: I18nKey) => string): string { return value === null ? t("unknownValue") : value.toLocaleString(); }
function ratio(value: number | null, t: (key: I18nKey) => string): string { return value === null ? t("unknownValue") : `${(value * 100).toFixed(1)}%`; }
function percent(value: number | null, total: number): number { return value === null ? 0 : Math.max(0, Math.min(100, (value / total) * 100)); }
