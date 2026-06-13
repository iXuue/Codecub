import type { BackendEvent } from "./backendEvents";

export type PendingApproval = {
  approvalId: string;
  runId: string;
  toolName: string;
  args: Record<string, unknown>;
  cwd: string;
  timeout: number | null;
  riskLevel: string;
  requestedAt: string;
  resolving: boolean;
};

export type ResolvedApproval = PendingApproval & {
  decision: string;
  reason: string;
  resolvedAt: string;
};

export type ApprovalState = {
  pending: PendingApproval[];
  history: ResolvedApproval[];
};

export function createInitialApprovalState(): ApprovalState {
  return {
    pending: [],
    history: [],
  };
}

export function markApprovalResolving(state: ApprovalState, approvalId: string): ApprovalState {
  return {
    ...state,
    pending: state.pending.map((approval) =>
      approval.approvalId === approvalId ? { ...approval, resolving: true } : approval,
    ),
  };
}

export function applyApprovalEvent(state: ApprovalState, event: BackendEvent): ApprovalState {
  if (event.type === "approval_requested") {
    const approval = approvalFromEvent(event);
    return {
      ...state,
      pending: [...state.pending.filter((item) => item.approvalId !== approval.approvalId), approval],
    };
  }

  if (event.type === "approval_resolved") {
    const approvalId = String(event.payload.approval_id ?? "");
    const existing = state.pending.find((item) => item.approvalId === approvalId);
    const fallback = approvalFromEvent(event);
    const resolved: ResolvedApproval = {
      ...(existing ?? fallback),
      decision: String(event.payload.decision ?? ""),
      reason: String(event.payload.reason ?? ""),
      resolvedAt: event.timestamp,
      resolving: false,
    };
    return {
      pending: state.pending.filter((item) => item.approvalId !== approvalId),
      history: [resolved, ...state.history].slice(0, 50),
    };
  }

  if (event.type === "run_canceled" || event.type === "run_failed") {
    return {
      ...state,
      pending: state.pending.filter((item) => item.runId !== event.run_id),
    };
  }

  return state;
}

function approvalFromEvent(event: BackendEvent): PendingApproval {
  const timeout = event.payload.timeout;
  return {
    approvalId: String(event.payload.approval_id ?? ""),
    runId: event.run_id,
    toolName: String(event.payload.tool_name ?? ""),
    args: toRecord(event.payload.args),
    cwd: String(event.payload.cwd ?? ""),
    timeout: typeof timeout === "number" ? timeout : null,
    riskLevel: String(event.payload.risk_level ?? ""),
    requestedAt: event.timestamp,
    resolving: false,
  };
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
