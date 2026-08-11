import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectHistoryPanel } from "../src/components/ProjectHistoryPanel";
import { t } from "../src/i18n";

describe("ProjectHistoryPanel", () => {
  it("renders a selected empty manual chat with localized copy", () => {
    render(
      <ProjectHistoryPanel
        t={(key) => t("en-US", key)}
        sessions={[
          {
            id: "manual-s1",
            title: "__codecub_empty_chat__",
            createdAt: "2026-06-15T00:00:00Z",
            updatedAt: "2026-06-15T00:00:01Z",
            messageCount: 0,
            preview: "",
          },
        ]}
        activeSessionId="manual-s1"
        error=""
        onRefresh={vi.fn()}
        onResume={vi.fn()}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("New chat")).toBeTruthy();
    expect(screen.getByText("New chat ready for the first task")).toBeTruthy();
    expect(screen.getByText("New chat").closest(".side-list-row")?.classList.contains("active")).toBe(true);
  });

  it("resumes a selected session", () => {
    const onResume = vi.fn();
    render(
      <ProjectHistoryPanel
        t={(key) => t("en-US", key)}
        sessions={[
          {
            id: "s1",
            title: "Fix tests",
            createdAt: "2026-06-15T00:00:00Z",
            updatedAt: "2026-06-15T00:00:01Z",
            messageCount: 2,
            preview: "All tests are green",
          },
        ]}
        activeSessionId=""
        error=""
        onRefresh={vi.fn()}
        onResume={onResume}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /All tests are green/i }));

    expect(onResume).toHaveBeenCalledWith("s1");
  });

  it("deletes a selected session from the row action", () => {
    const onDelete = vi.fn();
    render(
      <ProjectHistoryPanel
        t={(key) => t("en-US", key)}
        sessions={[
          {
            id: "s1",
            title: "Refactor UI",
            createdAt: "2026-06-15T00:00:00Z",
            updatedAt: "2026-06-15T00:00:01Z",
            messageCount: 1,
            preview: "",
          },
        ]}
        activeSessionId="s1"
        error=""
        onRefresh={vi.fn()}
        onResume={vi.fn()}
        onCreate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Delete chat Refactor UI/i }));

    expect(onDelete).toHaveBeenCalledWith("s1");
  });
});
