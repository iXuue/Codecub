import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CubStatusChip } from "../src/components/CubStatusChip";
import { t } from "../src/i18n";
import type { ChatState } from "../src/state/chatState";

function state(phase: string, label = phase): ChatState {
  return {
    messages: [],
    activeRunId: "r1",
    isRunning: Boolean(phase),
    runStatus: phase
      ? { runId: "r1", phase, label, detail: "", startedAt: "", elapsedMs: 1200, updatedAt: "" }
      : null,
  };
}

describe("CubStatusChip", () => {
  it("shows ready when no run is active", () => {
    render(<CubStatusChip t={(key) => t("en-US", key)} chatState={state("")} />);
    expect(screen.getByText("Ready")).toBeTruthy();
  });

  it("shows safe observable run status and elapsed time", () => {
    render(<CubStatusChip t={(key) => t("en-US", key)} chatState={state("model_streaming", "Receiving model response")} />);
    expect(screen.getByText("Receiving model response")).toBeTruthy();
    expect(screen.getByText("00:01")).toBeTruthy();
  });
});
