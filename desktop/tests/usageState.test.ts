import { describe, expect, it } from "vitest";
import { applyUsageEvent, createInitialUsageState } from "../src/state/usageState";
import type { BackendEvent } from "../src/state/backendEvents";

function event(type: string, groups: unknown[], revision = 1): BackendEvent {
  const snapshot = { schema_version: 2, revision, run_id: "r1", groups };
  return { type, timestamp: "2026-08-03T00:00:00Z", session_id: "s1", run_id: "r1", payload: type === "usage_updated" ? { run_snapshot: snapshot, session_snapshot: { ...snapshot, scope: "session", run_id: "" } } : { ...snapshot, scope: "session", run_id: "" } };
}

const group = (key: string, input: number) => ({
  aggregation_key: key,
  connection: { model: key },
  calculation_channels: { context: key },
  context: { actual_input_tokens: input },
  cache: { read_tokens: 0, write_tokens: null, uncached_input_tokens: input, read_ratio: 0 },
  output: { output_tokens: 1, reasoning_tokens: 0 },
  cost: [], request_count: 1, warnings: [],
});

describe("usageState", () => {
  it("keeps incompatible calculation groups separate", () => {
    let state = createInitialUsageState();
    state = applyUsageEvent(state, event("usage_updated", [group("codex-responses", 100)], 1));
    state = applyUsageEvent(state, event("usage_updated", [group("codex-responses", 100), group("claude-messages", 50)], 2));
    expect(state.runGroups).toHaveLength(2);
    expect(state.sessionGroups).toHaveLength(2);
  });

  it("resets only run usage when a new request starts", () => {
    let state = applyUsageEvent(createInitialUsageState(), event("usage_updated", [group("codex", 100)]));
    state = applyUsageEvent(state, { ...event("user_message_received", []), payload: {} });
    expect(state.runGroups).toEqual([]);
    expect(state.sessionGroups).toHaveLength(1);
  });
});
