import { describe, expect, it, vi } from "vitest";
import { sendToRenderer, type RendererWindow } from "../electron/safeIpc";

function createRendererWindow(options: {
  windowDestroyed?: boolean;
  webContentsDestroyed?: boolean;
  send?: (channel: string, ...args: unknown[]) => void;
} = {}): RendererWindow {
  return {
    isDestroyed: () => options.windowDestroyed ?? false,
    webContents: {
      isDestroyed: () => options.webContentsDestroyed ?? false,
      send: options.send ?? vi.fn(),
    },
  };
}

describe("sendToRenderer", () => {
  it("sends events to a live renderer", () => {
    const send = vi.fn();
    const window = createRendererWindow({ send });

    const sent = sendToRenderer(window, "backend:event", "payload");

    expect(sent).toBe(true);
    expect(send).toHaveBeenCalledWith("backend:event", "payload");
  });

  it("skips sending after the window is destroyed", () => {
    const send = vi.fn();
    const window = createRendererWindow({ windowDestroyed: true, send });

    const sent = sendToRenderer(window, "backend:error", "closed");

    expect(sent).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it("skips sending after webContents is destroyed", () => {
    const send = vi.fn();
    const window = createRendererWindow({ webContentsDestroyed: true, send });

    const sent = sendToRenderer(window, "terminal:data", "term-1", "output");

    expect(sent).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it("swallows Electron destroyed-object races during shutdown", () => {
    const window = createRendererWindow({
      send: () => {
        throw new Error("Object has been destroyed");
      },
    });

    expect(sendToRenderer(window, "backend:event", "late-output")).toBe(false);
  });

  it("rethrows unrelated send failures", () => {
    const window = createRendererWindow({
      send: () => {
        throw new Error("unexpected ipc failure");
      },
    });

    expect(() => sendToRenderer(window, "backend:event", "payload")).toThrow("unexpected ipc failure");
  });
});
