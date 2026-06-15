import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExtensionsPanel } from "../src/components/ExtensionsPanel";
import { t } from "../src/i18n";

describe("ExtensionsPanel", () => {
  it("shows installed extensions and triggers install actions", () => {
    const onInstallSkill = vi.fn();
    const onInstallPlugin = vi.fn();
    render(
      <ExtensionsPanel
        t={(key) => t("en-US", key)}
        extensions={{
          skills: [{ id: "local-skill", kind: "skill", name: "Local Skill", path: "D:/repo/.codecub/skills/local-skill", installedAt: "" }],
          plugins: [{ id: "local-plugin", kind: "plugin", name: "Local Plugin", path: "D:/repo/.codecub/plugins/local-plugin", installedAt: "" }],
        }}
        error=""
        onRefresh={vi.fn()}
        onInstallSkill={onInstallSkill}
        onInstallPlugin={onInstallPlugin}
      />,
    );

    expect(screen.getByText("Local Skill")).toBeTruthy();
    expect(screen.getByText("Local Plugin")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Install Skill" }));
    fireEvent.click(screen.getByRole("button", { name: "Install Plugin" }));

    expect(onInstallSkill).toHaveBeenCalled();
    expect(onInstallPlugin).toHaveBeenCalled();
  });
});
