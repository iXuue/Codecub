import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChatActivityStream } from "../src/components/ChatActivityStream";
import { t } from "../src/i18n";
import type { BackendEvent } from "../src/state/backendEvents";
import type { ChatState } from "../src/state/chatState";

function event(type: BackendEvent["type"], payload: Record<string, unknown> = {}, timestamp = "2026-06-16T12:00:00.000Z"): BackendEvent {
  return {
    type,
    timestamp,
    session_id: "session-1",
    run_id: "run-1",
    payload,
  };
}

function chatState(isRunning = true): ChatState {
  return {
    activeRunId: "run-1",
    isRunning,
    runStatus: {
      runId: "run-1",
      phase: "model_streaming",
      label: "Streaming",
      detail: "",
      startedAt: "2026-06-16T11:59:50.000Z",
      elapsedMs: 10000,
      updatedAt: "2026-06-16T12:00:00.000Z",
    },
    messages: [],
  };
}

describe("ChatActivityStream", () => {
  it("renders command and file-change activity as readable text", () => {
    render(
      <ChatActivityStream
        t={(key) => t("en-US", key)}
        chatState={chatState(false)}
        events={[
          event("user_message_received", { message: "Update the UI" }),
          event("tool_result", { tool_name: "shell", command: "npm run test", status: "ok" }, "2026-06-16T12:00:01.000Z"),
          event("diff_summary", { diff_summary: [{ path: "desktop/src/App.tsx" }] }, "2026-06-16T12:00:02.000Z"),
          event("run_completed", {}, "2026-06-16T12:00:03.000Z"),
        ]}
      />,
    );

    expect(screen.getByText("Processed")).toBeTruthy();
    expect(screen.getByText("Ran shell")).toBeTruthy();
    expect(screen.getByText("npm run test")).toBeTruthy();
    expect(screen.getByText("Edited 1 files")).toBeTruthy();
    expect(screen.getAllByText("Task completed").length).toBeGreaterThan(0);
  });

  it("collapses and expands activity details from the summary button", () => {
    render(
      <ChatActivityStream
        t={(key) => t("en-US", key)}
        chatState={chatState(false)}
        events={[
          event("user_message_received", { message: "Update the UI" }),
          event("tool_result", { tool_name: "shell", command: "npm run test", status: "ok" }, "2026-06-16T12:00:01.000Z"),
          event("run_completed", {}, "2026-06-16T12:00:02.000Z"),
        ]}
      />,
    );

    const summary = screen.getByRole("button", { name: /Processed/ });
    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(summary.textContent).toContain("Task completed");
    expect(screen.getByText("npm run test")).toBeTruthy();

    fireEvent.click(summary);

    expect(summary.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("npm run test")).toBeNull();

    fireEvent.click(summary);

    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("npm run test")).toBeTruthy();
  });

  it("renders structured tool arguments and result instead of object coercion", () => {
    render(
      <ChatActivityStream
        t={(key) => t("en-US", key)}
        chatState={chatState(false)}
        events={[
          event("tool_result", { tool_name: "search", args: { pattern: "memory", path: "." }, result: "no matches", status: "error" }),
        ]}
      />,
    );

    expect(screen.getByText('{"pattern":"memory","path":"."}')).toBeTruthy();
    expect(screen.getByText("error: no matches")).toBeTruthy();
    expect(screen.queryByText("[object Object]")).toBeNull();
  });

  it("shows the current translated activity without rendering raw assistant chunks", () => {
    render(
      <ChatActivityStream
        t={(key) => t("en-US", key)}
        chatState={chatState(true)}
        events={[
          event("user_message_received", { message: "Refactor" }),
          event("assistant_delta", { text: "partial token" }, "2026-06-16T12:00:01.000Z"),
        ]}
      />,
    );

    expect(screen.getAllByText("Receiving model response").length).toBeGreaterThan(0);
    expect(screen.queryByText("partial token")).toBeNull();
  });

  it("shows a stalled heartbeat as a readable current activity", () => {
    render(
      <ChatActivityStream
        t={(key) => t("en-US", key)}
        chatState={chatState(true)}
        events={[
          event("user_message_received", { message: "Inspect this repository" }),
          event(
            "run_status",
            { phase: "checking_workspace", label: "Checking repository state", heartbeat: true, silent_for_ms: 90_000 },
            "2026-06-16T12:01:30.000Z",
          ),
        ]}
      />,
    );

    expect(screen.getAllByText("Checking repository state").length).toBeGreaterThan(0);
    expect(screen.getByText(/No new backend step for a while/)).toBeTruthy();
  });
});
