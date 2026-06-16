import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import { t } from "../src/i18n";
import type { ChatState } from "../src/state/chatState";

describe("ChatView layout", () => {
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

    render(<ChatView t={(key) => t("en-US", key)} chatState={chatState} onSend={vi.fn()} onStop={vi.fn()} />);
    expect(screen.getByText("Change the UI").closest(".message")?.classList.contains("user")).toBe(true);
    expect(screen.getByText("I will update the layout.").closest(".message")?.classList.contains("assistant")).toBe(true);
  });
});
