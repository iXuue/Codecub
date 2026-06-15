import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectHistoryPanel } from "../src/components/ProjectHistoryPanel";
import { t } from "../src/i18n";

describe("ProjectHistoryPanel", () => {
  it("resumes a selected session", () => {
    const onResume = vi.fn();
    render(
      <ProjectHistoryPanel
        t={(key) => t("en-US", key)}
        sessions={[
          {
            id: "s1",
            createdAt: "2026-06-15T00:00:00Z",
            updatedAt: "2026-06-15T00:00:01Z",
            messageCount: 2,
            preview: "Fix tests",
          },
        ]}
        error=""
        onRefresh={vi.fn()}
        onResume={onResume}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Fix tests/i }));

    expect(onResume).toHaveBeenCalledWith("s1");
  });
});
