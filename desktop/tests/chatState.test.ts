import { describe, expect, it } from "vitest";
import { applyBackendEvent, createInitialChatState } from "../src/state/chatState";

describe("applyBackendEvent", () => {
  it("records user and assistant messages from backend events", () => {
    let state = createInitialChatState();

    state = applyBackendEvent(state, {
      type: "user_message_received",
      timestamp: "2026-06-11T00:00:00Z",
      session_id: "s1",
      run_id: "r1",
      payload: { message: "你好" },
    });

    state = applyBackendEvent(state, {
      type: "assistant_message",
      timestamp: "2026-06-11T00:00:01Z",
      session_id: "s1",
      run_id: "r1",
      payload: { text: "你好，我是 CodeCub。" },
    });

    expect(state.messages).toHaveLength(2);
    expect(state.messages[0].role).toBe("user");
    expect(state.messages[1].role).toBe("assistant");
    expect(state.messages[1].content).toContain("CodeCub");
  });
});
