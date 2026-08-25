import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WelcomePage } from "../src/components/WelcomePage";
import { t } from "../src/i18n";

describe("WelcomePage", () => {
  it("opens a recent project directly when clicked", () => {
    const onOpenRecentProject = vi.fn();
    render(
      <WelcomePage
        t={(key) => t("en-US", key)}
        recentProjects={[
          {
            path: "D:/repo",
            name: "repo",
            lastSessionId: "",
            lastUsedAt: "2026-06-15T00:00:00Z",
          },
        ]}
        onOpenProject={vi.fn()}
        onOpenRecentProject={onOpenRecentProject}
        onSettings={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /repo/i }));

    expect(onOpenRecentProject).toHaveBeenCalledWith("D:/repo");
  });
});
