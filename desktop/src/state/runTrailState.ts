import type { BackendEvent } from "./backendEvents";
import type { ChatState } from "./chatState";

export type RunTrailStepId = "context" | "model" | "tool" | "diff" | "done";
export type RunTrailStepState = "pending" | "active" | "complete";
export type RunTrailStep = { id: RunTrailStepId; state: RunTrailStepState };

const steps: RunTrailStepId[] = ["context", "model", "tool", "diff", "done"];

export function deriveRunTrail(chatState: ChatState, events: BackendEvent[]): RunTrailStep[] {
  const active = new Set<RunTrailStepId>();
  const phase = chatState.runStatus?.phase ?? "";

  if (chatState.isRunning || phase || events.length > 0) {
    active.add("context");
  }
  if (phase.includes("model") || phase === "finalizing" || hasEvent(events, "assistant_delta") || hasEvent(events, "assistant_message")) {
    active.add("context");
    active.add("model");
  }
  if (phase.includes("tool") || hasEvent(events, "tool_started") || hasEvent(events, "tool_result")) {
    active.add("context");
    active.add("model");
    active.add("tool");
  }
  if (hasEvent(events, "diff_summary")) {
    active.add("context");
    active.add("model");
    active.add("tool");
    active.add("diff");
  }
  if (hasEvent(events, "run_completed") || phase === "completed") {
    active.add("context");
    active.add("model");
    active.add("done");
  }

  const current = [...active].at(-1);
  return steps.map((id) => ({
    id,
    state: !active.has(id) ? "pending" : id === current && chatState.isRunning ? "active" : "complete",
  }));
}

function hasEvent(events: BackendEvent[], type: BackendEvent["type"]): boolean {
  return events.some((event) => event.type === type);
}
