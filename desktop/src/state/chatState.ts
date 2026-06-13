import type { BackendEvent } from "./backendEvents";

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  runId: string;
  createdAt: string;
};

export type ChatState = {
  messages: ChatMessage[];
  activeRunId: string;
  isRunning: boolean;
};

export function createInitialChatState(): ChatState {
  return {
    messages: [],
    activeRunId: "",
    isRunning: false,
  };
}

export function applyBackendEvent(state: ChatState, event: BackendEvent): ChatState {
  if (event.type === "user_message_received") {
    return {
      ...state,
      activeRunId: event.run_id,
      isRunning: true,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:user:${state.messages.length}`,
          role: "user",
          content: String(event.payload.message ?? ""),
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }

  if (event.type === "assistant_message") {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:assistant:${state.messages.length}`,
          role: "assistant",
          content: String(event.payload.text ?? event.payload.final ?? ""),
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }

  if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_canceled") {
    return {
      ...state,
      activeRunId: "",
      isRunning: false,
    };
  }

  return state;
}
