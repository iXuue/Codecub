export type BackendEventType =
  | "session_started"
  | "session_closed"
  | "user_message_received"
  | "run_status"
  | "assistant_delta"
  | "assistant_message"
  | "run_completed"
  | "run_failed"
  | "run_canceled"
  | "tool_result"
  | "approval_requested"
  | "approval_resolved"
  | "diff_summary"
  | "legacy_import_detected"
  | "legacy_import_completed"
  | "legacy_import_failed"
  | string;

export type BackendEvent = {
  type: BackendEventType;
  timestamp: string;
  session_id: string;
  run_id: string;
  payload: Record<string, unknown>;
};

const requiredFields = ["type", "timestamp", "session_id", "run_id"] as const;

export function parseBackendEventLine(line: string): BackendEvent {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch (error) {
    throw new Error("Invalid backend event JSON", { cause: error });
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Backend event must be an object");
  }

  const event = parsed as Partial<BackendEvent>;
  if (typeof event.type !== "string") {
    throw new Error(`Backend event missing required field: ${requiredFields[0]}`);
  }
  if (typeof event.timestamp !== "string") {
    throw new Error(`Backend event missing required field: ${requiredFields[1]}`);
  }
  if (typeof event.session_id !== "string") {
    throw new Error(`Backend event missing required field: ${requiredFields[2]}`);
  }
  if (typeof event.run_id !== "string") {
    throw new Error(`Backend event missing required field: ${requiredFields[3]}`);
  }

  return {
    type: event.type,
    timestamp: event.timestamp,
    session_id: event.session_id,
    run_id: event.run_id,
    payload: isRecord(event.payload) ? event.payload : {},
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
