import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TerminalPanel } from "../src/components/TerminalPanel";
import { t } from "../src/i18n";
import type { TerminalErrorEvent } from "../electron/ipcTypes";

const terminalFocus = vi.fn();
const terminalBlur = vi.fn();
const terminalScrollToBottom = vi.fn();
const terminalOnData = vi.fn();

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    open = vi.fn();
    write = vi.fn((_data: string, callback?: () => void) => callback?.());
    onData = terminalOnData;
    dispose = vi.fn();
    focus = terminalFocus;
    blur = terminalBlur;
    scrollToBottom = terminalScrollToBottom;
  },
}));

describe("TerminalPanel", () => {
  beforeEach(() => {
    terminalFocus.mockClear();
    terminalBlur.mockClear();
    terminalScrollToBottom.mockClear();
    terminalOnData.mockClear();
  });

  it("renders as a collapsed dock before the terminal starts", () => {
    (window as unknown as { codecub: unknown }).codecub = ({
      startTerminal: vi.fn(),
      writeTerminal: vi.fn(),
      closeTerminal: vi.fn(),
      onTerminalData: vi.fn(() => () => undefined),
      onTerminalExit: vi.fn(() => () => undefined),
      onTerminalError: vi.fn(() => () => undefined),
    } as unknown) as Partial<Window["codecub"]>;

    render(<TerminalPanel t={(key) => t("en-US", key)} projectPath="D:/repo" />);

    expect(screen.getByLabelText("Terminal").classList.contains("collapsed")).toBe(true);
    expect(screen.getAllByText("Terminal is not running")[0]?.closest(".terminal-title")).toBeTruthy();
  });

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

  it("focuses the terminal and can run codecub from the terminal toolbar", async () => {
    const writeTerminal = vi.fn();
    (window as unknown as { codecub: unknown }).codecub = ({
      startTerminal: vi.fn(),
      writeTerminal,
      closeTerminal: vi.fn(),
      onTerminalData: vi.fn(() => () => undefined),
      onTerminalExit: vi.fn(() => () => undefined),
      onTerminalError: vi.fn(() => () => undefined),
    } as unknown) as Partial<Window["codecub"]>;

    render(<TerminalPanel t={(key) => t("en-US", key)} projectPath="D:/repo" />);
    fireEvent.click(screen.getByRole("button", { name: "Open Terminal" }));

    await waitFor(() => expect(window.codecub.startTerminal).toHaveBeenCalledWith(expect.objectContaining({ cwd: "D:/repo" })));
    await waitFor(() => expect(terminalFocus).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Hide Terminal" }));
    expect(screen.getByLabelText("Terminal").classList.contains("collapsed")).toBe(true);
    expect(terminalBlur).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Run CodeCub" }));

    expect(writeTerminal).toHaveBeenCalledWith(expect.objectContaining({ data: "codecub\r" }));
    expect(screen.getByLabelText("Terminal").classList.contains("expanded")).toBe(true);
  });
});
