import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TerminalPanel } from "../src/components/TerminalPanel";
import { t } from "../src/i18n";
import type { TerminalErrorEvent } from "../electron/ipcTypes";

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    open = vi.fn();
    write = vi.fn();
    onData = vi.fn();
    dispose = vi.fn();
  },
}));

describe("TerminalPanel", () => {
  it("shows terminal error events", async () => {
    let terminalErrorCallback: ((event: TerminalErrorEvent) => void) | null = null;
    const uuid = "00000000-0000-4000-8000-000000000000";
    (window as unknown as { codecub: unknown }).codecub = ({
      startTerminal: vi.fn(async () => {
        terminalErrorCallback?.({ terminalId: `terminal-${uuid}`, message: "Terminal cwd does not exist: D:/missing" });
      }),
      writeTerminal: vi.fn(),
      closeTerminal: vi.fn(),
      onTerminalData: vi.fn(() => () => undefined),
      onTerminalExit: vi.fn(() => () => undefined),
      onTerminalError: vi.fn((callback) => {
        terminalErrorCallback = callback;
        return () => undefined;
      }),
    } as unknown) as Partial<Window["codecub"]>;
    vi.spyOn(crypto, "randomUUID").mockReturnValue(uuid);

    render(<TerminalPanel t={(key) => t("en-US", key)} projectPath="D:/missing" />);
    fireEvent.click(screen.getByRole("button", { name: "Open Terminal" }));

    expect(await screen.findByText("Terminal cwd does not exist: D:/missing")).toBeTruthy();
    await waitFor(() => expect(window.codecub.startTerminal).toHaveBeenCalled());
  });
});
