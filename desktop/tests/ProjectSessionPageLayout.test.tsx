import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectSessionPage } from "../src/components/ProjectSessionPage";
import { t } from "../src/i18n";
import { createInitialApprovalState } from "../src/state/approvalState";
import { createInitialChatState } from "../src/state/chatState";
import { createInitialUsageState } from "../src/state/usageState";

function installCodecubMock() {
  (window as unknown as { codecub: unknown }).codecub = {
    loadGitStatus: vi.fn(async () => ({ branch: "main", dirty: false, changedCount: 0, ahead: 0, behind: 0, files: [] })),
    startTerminal: vi.fn(),
    writeTerminal: vi.fn(),
    resizeTerminal: vi.fn(),
    closeTerminal: vi.fn(),
    onTerminalData: vi.fn(() => () => undefined),
    onTerminalExit: vi.fn(() => () => undefined),
    onTerminalError: vi.fn(() => () => undefined),
  } as unknown as Partial<Window["codecub"]>;
}

describe("ProjectSessionPage layout", () => {
  it("renders project sidebar, center workbench, and run inspector", () => {
    installCodecubMock();
    render(
      <ProjectSessionPage
        t={(key) => t("en-US", key)}
        projectPath="D:/repo"
        events={[]}
        chatState={createInitialChatState()}
        approvalState={createInitialApprovalState()}
        projectSessions={[]}
        activeSessionId=""
        sessionError=""
        extensions={{ skills: [], plugins: [] }}
        extensionError=""
        backendError=""
        usageState={createInitialUsageState()}
        onSend={vi.fn()}
        onStop={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onImportLegacy={vi.fn()}
        onRefreshSessions={vi.fn()}
        onResumeSession={vi.fn()}
        onCreateSession={vi.fn()}
        onDeleteSession={vi.fn()}
        onRefreshExtensions={vi.fn()}
        onInstallSkill={vi.fn()}
        onInstallPlugin={vi.fn()}
        onSettings={vi.fn()}
        onBackHome={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Project context")).toBeTruthy();
    expect(screen.getByLabelText("Workbench")).toBeTruthy();
    expect(screen.getByLabelText("Run inspector")).toBeTruthy();
    expect(document.querySelector(".project-sidebar")).toBeTruthy();
    expect(document.querySelector(".message-list")).toBeTruthy();
    expect(document.querySelector(".run-inspector")).toBeTruthy();
  });
});
