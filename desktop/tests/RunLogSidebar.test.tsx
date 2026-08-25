import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunLogSidebar } from "../src/components/RunLogSidebar";
import { t } from "../src/i18n";
import type { BackendEvent } from "../src/state/backendEvents";

function event(type: BackendEvent["type"], payload: Record<string, unknown> = {}): BackendEvent {
  return {
    type,
    timestamp: "2026-06-16T12:28:02.997258Z",
    session_id: "20260616-202802-4249b1",
    run_id: "run-1234567890abcdef",
    payload,
  };
}

describe("RunLogSidebar", () => {
  it("renders translated readable event cards by default", () => {
    render(
      <RunLogSidebar
        t={(key) => t("zh-CN", key)}
        events={[
          event("session_started", {
            cwd: "D:/repo/CodeCub",
            approval_policy: "ask",
            session_path: "D:/repo/CodeCub/.codecub/sessions/s1.json",
          }),
          event("run_status", { phase: "context", label: "Building context", elapsed_ms: 1200 }),
        ]}
      />,
    );

    expect(screen.getByText("会话已开始")).toBeTruthy();
    expect(screen.getByText("运行状态")).toBeTruthy();
    expect(screen.getByText("正在整理上下文")).toBeTruthy();
    expect(screen.getByText(/耗时 1.2s/)).toBeTruthy();
    expect(screen.queryByText(/"cwd"/)).toBeNull();
  });

  it("keeps streamed assistant chunks out of the default readable log", () => {
    render(<RunLogSidebar t={(key) => t("zh-CN", key)} events={[event("assistant_delta", { text: "hello" })]} />);

    expect(screen.getByText("暂无可读日志")).toBeTruthy();
    expect(screen.queryByText("模型回复片段")).toBeNull();
  });

  it("hides heartbeat status cards by default and shows them in debug mode", () => {
    render(
      <RunLogSidebar
        t={(key) => t("en-US", key)}
        events={[event("run_status", { phase: "building_context", label: "Building context", heartbeat: true, silent_for_ms: 70_000 })]}
      />,
    );

    expect(screen.getByText("No readable log events yet")).toBeTruthy();
    expect(screen.queryByText("Run status")).toBeNull();

    fireEvent.click(screen.getByLabelText(/Debug/i));

    expect(screen.getByText("Run status")).toBeTruthy();
    expect(screen.getByText(/No new backend step for a while/)).toBeTruthy();
    expect(screen.getByText("Raw event")).toBeTruthy();
  });

  it("collapses repeated run status cards in the readable log", () => {
    render(
      <RunLogSidebar
        t={(key) => t("en-US", key)}
        events={[
          event("run_status", { phase: "checking_workspace", label: "Checking repository state" }),
          event("run_status", { phase: "checking_workspace", label: "Checking repository state" }),
        ]}
      />,
    );

    expect(screen.getAllByText("Run status")).toHaveLength(1);
    expect(screen.getByText("Checking repository state")).toBeTruthy();
  });

  it("shows raw events only in debug mode", () => {
    render(<RunLogSidebar t={(key) => t("en-US", key)} events={[event("tool_result", { tool_name: "read_file", output: "ok" })]} />);

    expect(screen.queryByText("Raw event")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Debug/i));

    expect(screen.getByText("Raw event")).toBeTruthy();
    expect(screen.getByText("Tool")).toBeTruthy();
    expect(screen.getAllByText("read_file").length).toBeGreaterThan(0);
  });

  it("uses a stable ASCII separator for tool status details", () => {
    render(<RunLogSidebar t={(key) => t("en-US", key)} events={[event("tool_result", { tool_name: "write_file", status: "ok" })]} />);

    expect(screen.getByText("write_file - ok")).toBeTruthy();
  });

  it("renders tool start events as readable activity instead of internal event names", () => {
    render(<RunLogSidebar t={(key) => t("en-US", key)} events={[event("tool_started", { tool_name: "list_files", title: "Checking project files" })]} />);

    expect(screen.getByText("Running tool")).toBeTruthy();
    expect(screen.getByText("Checking project files")).toBeTruthy();
    expect(screen.getByText("list_files")).toBeTruthy();
    expect(screen.queryByText("tool_started")).toBeNull();
    expect(screen.queryByText("Unknown event")).toBeNull();
  });
});
