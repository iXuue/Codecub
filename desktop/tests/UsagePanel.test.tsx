import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsagePanel } from "../src/components/UsagePanel";
import { t } from "../src/i18n";
import type { UsageState } from "../src/state/usageState";

function state(mode: string): UsageState {
  return { sessionGroups: [], runGroups: [{
    aggregation_key: "g1",
    connection: { model: "gpt", api_operator: "right.codes", endpoint_kind: "chat_completions", connection_type: "relay", verification_status: "unverified" },
    calculation_channels: { context: "rightcode_codex_chat_usage" },
    context: { total_actual_input_tokens: 100, latest_actual_input_tokens: 100 }, cache: { mode, read_tokens: null, write_tokens: null, uncached_input_tokens: 100, read_ratio: null },
    output: { output_tokens: 10, reasoning_tokens: null }, cost: [], request_count: 1, warnings: [],
  }], sessionRevision: 0, runRevision: 0, sessionId: "s1", runId: "r1" };
}

describe("UsagePanel", () => {
  it("shows unavailable cache data instead of a zero cache rate for Right Code chat", () => {
    render(<UsagePanel t={(key) => t("en-US", key)} usageState={state("unavailable")} />);
    expect(screen.getByText("Some fields are unverified; unknown values are not treated as zero.")).toBeTruthy();
    expect(screen.queryByText("0.0%")).toBeNull();
    expect(screen.getByText("Awaiting operator billing data")).toBeTruthy();
  });

  it("shows evidence and separates latest, peak, and cumulative context", () => {
    const usageState = state("provider_managed");
    usageState.runGroups[0].connection = { ...usageState.runGroups[0].connection, endpoint_verification_status: "verified", usage_schema_verification_status: "unverified" };
    usageState.runGroups[0].context = { total_actual_input_tokens: 180000, latest_actual_input_tokens: 60000, peak_actual_input_tokens: 70000, context_window: 128000, peak_utilization_ratio: 70000 / 128000 };
    render(<UsagePanel t={(key) => t("en-US", key)} usageState={usageState} />);
    expect(screen.getByText("Latest input")).toBeTruthy();
    expect(screen.getByText("Peak context")).toBeTruthy();
    expect(screen.getByText("Total input")).toBeTruthy();
    expect(screen.getByText("Endpoint: Verified")).toBeTruthy();
    expect(screen.getByText("Usage data: Unverified")).toBeTruthy();
  });
});
