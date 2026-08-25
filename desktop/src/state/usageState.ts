import type { BackendEvent } from "./backendEvents";

export type UsageCost = {
  kind: string;
  amount: string;
  unit: string;
  unit_kind?: "fiat" | "operator_credit";
  source?: string;
  quality?: string;
  pricing_version?: string;
};

export type UsageGroup = {
  aggregation_key: string;
  connection: Record<string, string>;
  calculation_channels: Record<string, string>;
  context: Record<string, unknown>;
  cache: Record<string, unknown>;
  output: Record<string, unknown>;
  cost: UsageCost[];
  request_count: number;
  warnings: string[];
};

export type UsageState = {
  runGroups: UsageGroup[];
  sessionGroups: UsageGroup[];
  runRevision: number;
  sessionRevision: number;
  sessionId: string;
  runId: string;
};

export function createInitialUsageState(): UsageState {
  return { runGroups: [], sessionGroups: [], runRevision: 0, sessionRevision: 0, sessionId: "", runId: "" };
}

export function applyUsageEvent(state: UsageState, event: BackendEvent): UsageState {
  if (event.type === "user_message_received") {
    return { ...state, runGroups: [], runRevision: 0, runId: event.run_id };
  }
  if (event.type === "usage_snapshot") {
    const snapshot = readSnapshot(event.payload);
    if (!snapshot || (state.sessionId && event.session_id !== state.sessionId) || snapshot.revision < state.sessionRevision) return state;
    return { ...state, sessionId: event.session_id, sessionGroups: snapshot.groups, sessionRevision: snapshot.revision, runGroups: [], runRevision: 0, runId: "" };
  }
  if (event.type !== "usage_updated") {
    return state;
  }
  const run = readSnapshot(event.payload.run_snapshot);
  const session = readSnapshot(event.payload.session_snapshot);
  if (!run || !session || (state.sessionId && event.session_id !== state.sessionId)) return state;
  return {
    runGroups: event.run_id === run.runId && run.revision >= state.runRevision ? run.groups : state.runGroups,
    sessionGroups: session.revision >= state.sessionRevision ? session.groups : state.sessionGroups,
    runRevision: event.run_id === run.runId && run.revision >= state.runRevision ? run.revision : state.runRevision,
    sessionRevision: session.revision >= state.sessionRevision ? session.revision : state.sessionRevision,
    sessionId: event.session_id,
    runId: event.run_id,
  };
}

function readGroups(value: unknown): UsageGroup[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is UsageGroup => Boolean(item) && typeof item === "object" && typeof item.aggregation_key === "string");
}

function readSnapshot(value: unknown): { groups: UsageGroup[]; revision: number; runId: string } | null {
  if (!value || typeof value !== "object") return null;
  const snapshot = value as Record<string, unknown>;
  if (snapshot.schema_version !== 2 || typeof snapshot.revision !== "number" || !Number.isInteger(snapshot.revision) || snapshot.revision < 0) return null;
  return { groups: readGroups(snapshot.groups), revision: snapshot.revision, runId: typeof snapshot.run_id === "string" ? snapshot.run_id : "" };
}
