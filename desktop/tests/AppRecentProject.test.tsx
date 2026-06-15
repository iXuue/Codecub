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
  const settings: AppSettings = { language: "en-US", approvalPolicy: "ask", provider };
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

    await waitFor(() => expect(window.codecub.startBackend).toHaveBeenCalledWith("D:/repo", "ask"));
    await waitFor(() => expect(screen.getAllByText("D:/repo").length).toBeGreaterThan(0));
    expect(await screen.findByText(/Bundled CodeCub backend executable is missing/)).toBeTruthy();
  });
});
