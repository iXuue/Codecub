import type { BackendEvent } from "./backendEvents";
import type { ProjectSessionDetail } from "../../electron/ipcTypes";

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  runId: string;
  createdAt: string;
};

export type RunStatus = {
  runId: string;
  phase: string;
  label: string;
  detail: string;
  startedAt: string;
  elapsedMs: number;
  updatedAt: string;
};

export type ChatState = {
  messages: ChatMessage[];
  activeRunId: string;
  isRunning: boolean;
  runStatus: RunStatus | null;
};

export function createInitialChatState(): ChatState {
  return {
    messages: [],
    activeRunId: "",
    isRunning: false,
    runStatus: null,
  };
}

export function createChatStateFromSession(detail: ProjectSessionDetail): ChatState {
  return {
    messages: detail.messages.map((message, index) => ({
      id: `${detail.id}:history:${index}`,
      role: message.role,
      content: message.content,
      runId: "",
      createdAt: message.createdAt,
    })),
    activeRunId: "",
    isRunning: false,
    runStatus: null,
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

  if (event.type === "run_status") {
    return {
      ...state,
      runStatus: {
        runId: event.run_id,
        phase: String(event.payload.phase ?? ""),
        label: String(event.payload.label ?? ""),
        detail: String(event.payload.detail ?? ""),
        startedAt: String(event.payload.started_at ?? ""),
        elapsedMs: Number(event.payload.elapsed_ms ?? 0),
        updatedAt: event.timestamp,
      },
    };
  }

  if (event.type === "assistant_delta") {
    return upsertAssistantMessage(state, event, String(event.payload.text ?? ""), "append");
  }

  if (event.type === "assistant_message") {
    return upsertAssistantMessage(state, event, String(event.payload.text ?? event.payload.final ?? ""), "replace");
  }

  if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_canceled") {
    return {
      ...state,
      activeRunId: "",
      isRunning: false,
      runStatus: state.runStatus
        ? {
            ...state.runStatus,
            phase: event.type.replace("run_", ""),
            updatedAt: event.timestamp,
          }
        : null,
    };
  }

  return state;
}

function findAssistantMessageIndex(messages: ChatMessage[], runId: string): number {
  return messages.findIndex((message) => message.role === "assistant" && message.runId === runId);
}

function upsertAssistantMessage(
  state: ChatState,
  event: BackendEvent,
  content: string,
  mode: "append" | "replace",
): ChatState {
  const index = findAssistantMessageIndex(state.messages, event.run_id);
  if (index < 0) {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:assistant:${state.messages.length}`,
          role: "assistant",
          content,
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }
  return {
    ...state,
    messages: state.messages.map((message, messageIndex) =>
      messageIndex === index ? { ...message, content: mode === "append" ? message.content + content : content } : message,
    ),
  };
}
