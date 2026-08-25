import { describe, expect, it } from "vitest";
import { applyBackendEvent, createChatStateFromSession, createInitialChatState } from "../src/state/chatState";

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

  it("appends assistant deltas into one active message and replaces it on final", () => {
    let state = createInitialChatState();

    state = applyBackendEvent(state, {
      type: "user_message_received",
      timestamp: "2026-06-15T00:00:00Z",
      session_id: "s1",
      run_id: "r1",
      payload: { message: "hello" },
    });
    state = applyBackendEvent(state, {
      type: "assistant_delta",
      timestamp: "2026-06-15T00:00:01Z",
      session_id: "s1",
      run_id: "r1",
      payload: { text: "Hel" },
    });
    state = applyBackendEvent(state, {
      type: "assistant_delta",
      timestamp: "2026-06-15T00:00:02Z",
      session_id: "s1",
      run_id: "r1",
      payload: { text: "lo" },
    });

    expect(state.messages.filter((message) => message.role === "assistant")).toHaveLength(1);
    expect(state.messages[1].content).toBe("Hello");

    state = applyBackendEvent(state, {
      type: "assistant_message",
      timestamp: "2026-06-15T00:00:03Z",
      session_id: "s1",
      run_id: "r1",
      payload: { text: "Hello" },
    });

    expect(state.messages.filter((message) => message.role === "assistant")).toHaveLength(1);
    expect(state.messages[1].content).toBe("Hello");
  });

  it("stores the latest active run status", () => {
    let state = createInitialChatState();

    state = applyBackendEvent(state, {
      type: "run_status",
      timestamp: "2026-06-15T00:00:01Z",
      session_id: "s1",
      run_id: "r1",
      payload: {
        phase: "model_streaming",
        label: "Receiving model response",
        detail: "qwen-flash",
        elapsed_ms: 1200,
      },
    });

    expect(state.runStatus?.phase).toBe("model_streaming");
    expect(state.runStatus?.label).toBe("Receiving model response");
    expect(state.runStatus?.elapsedMs).toBe(1200);
  });

  it("creates chat state from loaded project session messages", () => {
    const state = createChatStateFromSession({
      id: "s1",
      messages: [
        { role: "user", content: "resume this", createdAt: "2026-06-15T00:00:00Z" },
        { role: "assistant", content: "loaded", createdAt: "2026-06-15T00:00:01Z" },
      ],
    });

    expect(state.isRunning).toBe(false);
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({ id: "s1:history:0", role: "user", content: "resume this" });
    expect(state.messages[1]).toMatchObject({ id: "s1:history:1", role: "assistant", content: "loaded" });
  });
});
