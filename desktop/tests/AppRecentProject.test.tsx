import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import type { AppSettings } from "../electron/ipcTypes";

const provider: AppSettings["provider"] = {
  provider: "openai",
  model: "qwen-flash",
  baseUrl: "https://www.right.codes/codex/v1",
  host: "http://127.0.0.1:11434",
  credential: { configured: false, source: "none", displayHint: "not configured" },
};

function installCodecubMock(overrides: Partial<Window["codecub"]> = {}) {
  const settings: AppSettings = {
    language: "en-US",
    approvalPolicy: "ask",
    appearance: { themeMode: "dark", accentColor: "#38BDF8" },
    provider,
  };
  (window as unknown as { codecub: unknown }).codecub = {
    openProject: vi.fn(),
    startBackend: vi.fn(async () => undefined),
    sendBackendCommand: vi.fn(),
    stopBackend: vi.fn(),
    loadSettings: vi.fn(async () => settings),
    saveSettings: vi.fn(async (nextSettings) => nextSettings),
    saveProviderSettings: vi.fn(async () => settings),
    clearProviderCredential: vi.fn(async () => settings),
    loadRecentProjects: vi.fn(async () => [
      {
        path: "D:/repo",
        name: "repo",
        lastSessionId: "",
        lastUsedAt: "2026-06-15T00:00:00Z",
      },
    ]),
    listProjectSessions: vi.fn(async () => []),
    createProjectSession: vi.fn(async () => ({ id: "manual-s1", createdAt: "2026-06-15T00:00:00Z" })),
    loadProjectSession: vi.fn(async () => ({ id: "s1", messages: [] })),
    deleteProjectSession: vi.fn(async () => ({ deleted: true })),
    listProjectExtensions: vi.fn(async () => ({ skills: [], plugins: [] })),
    installProjectSkill: vi.fn(async () => ({ canceled: false })),
    installProjectPlugin: vi.fn(async () => ({ canceled: false })),
    loadGitStatus: vi.fn(async () => ({ branch: "main", dirty: false, changedCount: 0, ahead: 0, behind: 0, files: [] })),
    startTerminal: vi.fn(),
    writeTerminal: vi.fn(),
    resizeTerminal: vi.fn(),
    closeTerminal: vi.fn(),
    onTerminalData: vi.fn(() => () => undefined),
    onTerminalExit: vi.fn(() => () => undefined),
    onTerminalError: vi.fn(() => () => undefined),
    onBackendEvent: vi.fn(() => () => undefined),
    onBackendError: vi.fn(() => () => undefined),
    ...overrides,
  } as Window["codecub"];
}

describe("App recent project flow", () => {
  it("enters the project page even when backend startup fails", async () => {
    installCodecubMock({
      startBackend: vi.fn(async () => {
        throw new Error("Bundled CodeCub backend executable is missing.");
      }),
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/i }));

    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenCalledWith("D:/repo", "ask", ""));
    await waitFor(() => expect(screen.getAllByText("D:/repo").length).toBeGreaterThan(0));
    expect(await screen.findByText(/Bundled CodeCub backend executable is missing/)).toBeTruthy();
  });

  it("renders the command deck launcher copy", async () => {
    installCodecubMock();

    render(<App />);

    expect(await screen.findByText("Command Deck")).toBeTruthy();
    expect(screen.getByText("Local coding agent")).toBeTruthy();
    expect(screen.getByText(/Give CodeCub a project/)).toBeTruthy();
  });

  it("continues the latest project and starts backend from the primary launcher action", async () => {
    installCodecubMock();

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Continue Latest Project/i }));

    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenCalledWith("D:/repo", "ask", ""));
    expect((await screen.findAllByText("repo")).length).toBeGreaterThan(0);
  });

  it("loads a selected project session and starts backend with resume id", async () => {
    installCodecubMock({
      listProjectSessions: vi.fn(async () => [
          {
            id: "s1",
            title: "previous task",
            createdAt: "2026-06-15T00:00:00Z",
            updatedAt: "2026-06-15T00:00:01Z",
          messageCount: 2,
          preview: "previous answer",
        },
      ]),
      loadProjectSession: vi.fn(async () => ({
        id: "s1",
        messages: [
          { role: "user" as const, content: "previous task", createdAt: "2026-06-15T00:00:00Z" },
          { role: "assistant" as const, content: "previous answer", createdAt: "2026-06-15T00:00:01Z" },
        ],
      })),
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/i }));
    fireEvent.click(await screen.findByRole("button", { name: /previous answer/i }));

    await waitFor(() => expect(window.codecub.loadProjectSession).toHaveBeenCalledWith("D:/repo", "s1"));
    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenLastCalledWith("D:/repo", "ask", "s1"));
    expect((await screen.findAllByText("previous task")).length).toBeGreaterThan(0);
  });

  it("creates a new chat session from the project sidebar", async () => {
    installCodecubMock();

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/i }));
    fireEvent.click(await screen.findByRole("button", { name: "New" }));

    await waitFor(() => expect(window.codecub.createProjectSession).toHaveBeenCalledWith("D:/repo"));
    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenLastCalledWith("D:/repo", "ask", "manual-s1"));
  });

  it("deletes the active chat after confirmation and starts a clean session", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    installCodecubMock({
      listProjectSessions: vi.fn(async () => [
        {
          id: "s1",
          title: "Delete me",
          createdAt: "2026-06-15T00:00:00Z",
          updatedAt: "2026-06-15T00:00:01Z",
          messageCount: 1,
          preview: "",
        },
      ]),
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^Delete me/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Delete chat Delete me/i }));

    await waitFor(() => expect(window.codecub.deleteProjectSession).toHaveBeenCalledWith("D:/repo", "s1"));
    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenLastCalledWith("D:/repo", "ask", ""));
    confirm.mockRestore();
  });

  it("keeps extension install errors visible after refreshing the extension list", async () => {
    installCodecubMock({
      installProjectSkill: vi.fn(async () => ({ canceled: false, error: "SKILL.md is missing" })),
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Install Skill" }));

    expect(await screen.findByText("SKILL.md is missing")).toBeTruthy();
  });
});
