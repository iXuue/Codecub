import { describe, expect, it } from "vitest";
import { parseBackendEventLine } from "../src/state/backendEvents";

describe("parseBackendEventLine", () => {
  it("parses a valid run_completed event", () => {
    const event = parseBackendEventLine(
      '{"type":"run_completed","timestamp":"2026-06-11T00:00:00Z","session_id":"s1","run_id":"r1","payload":{"final":"done"}}',
    );

    expect(event.type).toBe("run_completed");
    expect(event.timestamp).toBe("2026-06-11T00:00:00Z");
    expect(event.session_id).toBe("s1");
    expect(event.run_id).toBe("r1");
    expect(event.payload.final).toBe("done");
  });

  it("rejects invalid JSON", () => {
    expect(() => parseBackendEventLine("{bad json")).toThrow("Invalid backend event JSON");
  });

  it("rejects missing required fields", () => {
    expect(() => parseBackendEventLine('{"type":"run_completed"}')).toThrow("Backend event missing required field");
  });
});
