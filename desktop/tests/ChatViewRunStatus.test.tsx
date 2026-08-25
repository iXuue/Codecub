import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import { t } from "../src/i18n";
import type { BackendEvent } from "../src/state/backendEvents";
import type { ChatState } from "../src/state/chatState";

describe("ChatView run status", () => {
  it("shows inline run activity and elapsed time", () => {
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
    const events: BackendEvent[] = [
      {
        type: "run_status",
        timestamp: "2026-06-15T00:00:01Z",
        session_id: "s1",
        run_id: "r1",
        payload: { phase: "model_streaming" },
      },
    ];

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={events} onSend={vi.fn()} onStop={vi.fn()} />);

    expect(screen.getAllByText("Receiving model response").length).toBeGreaterThan(0);
    expect(screen.getByText(/5s/)).toBeTruthy();
    vi.useRealTimers();
  });

  it("scrolls the message list to the latest activity", () => {
    let frameCallback: ((time: number) => void) | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback: FrameRequestCallback) => {
      frameCallback = (time: number) => callback(time);
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
    const chatState: ChatState = {
      messages: [
        { id: "m1", role: "user", content: "hello", runId: "r1", createdAt: "2026-06-15T00:00:00Z" },
      ],
      activeRunId: "r1",
      isRunning: true,
      runStatus: {
        runId: "r1",
        phase: "building_context",
        label: "Building context",
        detail: "",
        startedAt: "2026-06-15T00:00:00Z",
        elapsedMs: 0,
        updatedAt: "2026-06-15T00:00:01Z",
      },
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={[]} onSend={vi.fn()} onStop={vi.fn()} />);

    const list = document.querySelector(".message-list") as HTMLDivElement;
    Object.defineProperty(list, "scrollHeight", { configurable: true, value: 480 });
    if (!frameCallback) {
      throw new Error("requestAnimationFrame was not scheduled");
    }
    frameCallback(0);

    expect(list.scrollTop).toBe(480);
  });
});
