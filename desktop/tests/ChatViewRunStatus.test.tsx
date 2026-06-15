import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import type { ChatState } from "../src/state/chatState";

const t = (key: string) => key;

describe("ChatView run status", () => {
  it("shows active run status and elapsed time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T00:00:05Z"));
    const chatState: ChatState = {
      messages: [],
      activeRunId: "r1",
      isRunning: true,
      runStatus: {
        runId: "r1",
        phase: "model_streaming",
        label: "Receiving model response",
        detail: "qwen-flash",
        startedAt: "2026-06-15T00:00:00Z",
        elapsedMs: 0,
        updatedAt: "2026-06-15T00:00:01Z",
      },
    };

    render(<ChatView t={t as never} chatState={chatState} onSend={vi.fn()} onStop={vi.fn()} />);

    expect(screen.getByText("Receiving model response")).toBeTruthy();
    expect(screen.getByText(/5s/)).toBeTruthy();
    vi.useRealTimers();
  });
});
