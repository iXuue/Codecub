import { describe, expect, it } from "vitest";
import { deriveRunTrail, type RunTrailStepId } from "../src/state/runTrailState";
import type { BackendEvent } from "../src/state/backendEvents";
import type { ChatState } from "../src/state/chatState";

function chat(phase: string): ChatState {
  return {
    messages: [],
    activeRunId: "r1",
    isRunning: true,
    runStatus: {
      runId: "r1",
      phase,
      label: phase,
      detail: "",
      startedAt: "",
      elapsedMs: 0,
      updatedAt: "2026-06-16T00:00:00Z",
    },
  };
}

function event(type: BackendEvent["type"]): BackendEvent {
  return { type, timestamp: "2026-06-16T00:00:00Z", session_id: "s1", run_id: "r1", payload: {} };
}

describe("deriveRunTrail", () => {
  it("marks context and model steps from run status", () => {
    const trail = deriveRunTrail(chat("model_streaming"), []);
    expect(activeIds(trail)).toEqual(["context", "model"]);
  });

  it("marks tool and diff steps from existing events", () => {
    const trail = deriveRunTrail(chat("tool_running"), [event("tool_started"), event("diff_summary")]);
    expect(activeIds(trail)).toEqual(["context", "model", "tool", "diff"]);
  });

  it("marks done when the run completes", () => {
    const trail = deriveRunTrail({ ...chat("completed"), isRunning: false }, [event("run_completed")]);
    expect(activeIds(trail)).toEqual(["context", "model", "done"]);
  });
});

function activeIds(trail: ReturnType<typeof deriveRunTrail>): RunTrailStepId[] {
  return trail.filter((step) => step.state !== "pending").map((step) => step.id);
}
