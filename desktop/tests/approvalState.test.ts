import { describe, expect, it } from "vitest";
import { applyApprovalEvent, createInitialApprovalState, markApprovalResolving } from "../src/state/approvalState";

describe("approvalState", () => {
  it("tracks requested and resolved approvals", () => {
    const requested = applyApprovalEvent(createInitialApprovalState(), {
      type: "approval_requested",
      session_id: "s",
      run_id: "run-1",
      timestamp: "2026-06-12T00:00:00Z",
      payload: { approval_id: "approval-1", tool_name: "write_file", args: { path: "README.md" } },
    });

    expect(requested.pending).toHaveLength(1);
    expect(requested.pending[0].approvalId).toBe("approval-1");
    expect(requested.pending[0].args.path).toBe("README.md");

    const resolving = markApprovalResolving(requested, "approval-1");
    expect(resolving.pending[0].resolving).toBe(true);

    const resolved = applyApprovalEvent(resolving, {
      type: "approval_resolved",
      session_id: "s",
      run_id: "run-1",
      timestamp: "2026-06-12T00:00:01Z",
      payload: { approval_id: "approval-1", decision: "approved" },
    });

    expect(resolved.pending).toHaveLength(0);
    expect(resolved.history[0].decision).toBe("approved");
    expect(resolved.history[0].toolName).toBe("write_file");
  });

  it("clears pending approvals for failed or canceled runs", () => {
    const state = applyApprovalEvent(createInitialApprovalState(), {
      type: "approval_requested",
      session_id: "s",
      run_id: "run-1",
      timestamp: "2026-06-12T00:00:00Z",
      payload: { approval_id: "approval-1", tool_name: "run_shell" },
    });

    const canceled = applyApprovalEvent(state, {
      type: "run_canceled",
      session_id: "s",
      run_id: "run-1",
      timestamp: "2026-06-12T00:00:02Z",
      payload: { reason: "user_requested" },
    });

    expect(canceled.pending).toHaveLength(0);
  });
});
