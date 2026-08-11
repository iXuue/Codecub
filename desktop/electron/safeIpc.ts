export type RendererWebContents = {
  isDestroyed: () => boolean;
  send: (channel: string, ...args: unknown[]) => void;
};

export type RendererWindow = {
  isDestroyed: () => boolean;
  webContents: RendererWebContents;
};

function isDestroyedObjectError(error: unknown): boolean {
  return error instanceof Error && error.message.includes("Object has been destroyed");
}

export function sendToRenderer(window: RendererWindow | null, channel: string, ...args: unknown[]): boolean {
  try {
    if (!window || window.isDestroyed()) {
      return false;
    }
    if (window.webContents.isDestroyed()) {
      return false;
    }
    window.webContents.send(channel, ...args);
    return true;
  } catch (error) {
    if (isDestroyedObjectError(error)) {
      return false;
    }
    throw error;
  }
}
