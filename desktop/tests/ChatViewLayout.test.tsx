import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import { t } from "../src/i18n";
import type { ChatState } from "../src/state/chatState";

describe("ChatView layout", () => {
  it("renders a focused empty state when no messages exist", () => {
    const chatState: ChatState = {
      activeRunId: "",
      isRunning: false,
      runStatus: null,
      messages: [],
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={[]} onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByText("What should CodeCub change?")).toBeTruthy();
  });

  it("renders an active empty chat state when a new session is selected", () => {
    const chatState: ChatState = {
      activeRunId: "",
      isRunning: false,
      runStatus: null,
      messages: [],
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={[]} activeSessionId="manual-s1" onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByText("New chat ready")).toBeTruthy();
  });

  it("keeps user and assistant messages visually distinguishable", () => {
    const chatState: ChatState = {
      activeRunId: "",
      isRunning: false,
      runStatus: null,
      messages: [
        { id: "u1", role: "user", content: "Change the UI", runId: "r1", createdAt: "" },
        { id: "a1", role: "assistant", content: "I will update the layout.", runId: "r1", createdAt: "" },
      ],
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={[]} onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByText("Change the UI").closest(".message")?.classList.contains("user")).toBe(true);
    expect(screen.getByText("I will update the layout.").closest(".message")?.classList.contains("assistant")).toBe(true);
  });

  it("focuses the task input when the composer surface is clicked", () => {
    const chatState: ChatState = {
      activeRunId: "",
      isRunning: false,
      runStatus: null,
      messages: [],
    };

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} events={[]} onSend={vi.fn()} onStop={vi.fn()} />);
    const textbox = screen.getByPlaceholderText("Describe a task for CodeCub to run in this project");
    const composer = textbox.closest(".composer");

    expect(composer).toBeTruthy();
    fireEvent.mouseDown(composer as HTMLElement);

    expect(document.activeElement).toBe(textbox);
  });
});
