import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("UI tokens", () => {
  it("defines the P0.9 glass appearance token baseline", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("--color-accent-user: #38BDF8");
    expect(css).toContain('--color-brand: var(--color-accent-user)');
    expect(css).toContain("--color-glass-surface: rgba(17, 24, 39, 0.62)");
    expect(css).toContain('.theme-root[data-theme="light"]');
    expect(css).toContain("--color-glass-surface: rgba(255, 255, 255, 0.58)");
    expect(css).toContain("--blur-glass: blur(24px) saturate(1.25)");
    expect(css).toContain("--radius-shell: 34px");
    expect(css).toContain("--radius-panel: 28px");
    expect(css).toContain("--radius-control: 18px");
    expect(css).toContain("--motion-fast: 120ms");
    expect(css).toContain("--motion-base: 180ms");
    expect(css).toContain("--font-code:");
  });

  it("defines integrated desktop chrome without a floating inner shell", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("Integrated desktop chrome: no floating inner shell");
    expect(css).toContain(".theme-root .app-shell");
    expect(css).toContain("border-radius: 0");
    expect(css).toContain("-webkit-app-region: drag");
    expect(css).toContain("-webkit-app-region: no-drag");
    expect(css).toContain(".window-controls");
    expect(css).toContain(".window-control.close:hover");
  });

  it("defines the terminal dock and workbench refinement layer", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("Terminal and workbench refinement: terminal as dock, chat as focus");
    expect(css).toContain(".chat-empty-state");
    expect(css).toContain(".terminal-dot");
    expect(css).toContain(".terminal-subtitle");
    expect(css).toContain(".theme-root .terminal-panel.expanded .terminal-surface");
    expect(css).toContain("height: 236px");
  });

  it("locks the outer shell and gives each workbench column its own scroll area", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("P0.16 independent scroll and observable activity pass");
    expect(css).toContain("height: 100vh");
    expect(css).toContain(".theme-root .workspace-layout");
    expect(css).toContain("overflow: hidden");
    expect(css).toContain(".theme-root .message-list");
    expect(css).toContain("overscroll-behavior: contain");
    expect(css).toContain(".activity-summary-current");
  });

  it("keeps the settings page scrollable inside the fixed desktop shell", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain(".theme-root .settings-page");
    expect(css).toContain("max-height: calc(100vh - 114px)");
    expect(css).toContain("overflow-y: auto");
    expect(css).toContain(".theme-root .settings-page::-webkit-scrollbar");
  });

  it("defines the P0.19 market polish layer for the home and workbench surfaces", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("P0.19 market polish visual pass");
    expect(css).toContain("--color-surface-raised");
    expect(css).toContain("--color-panel-line");
    expect(css).toContain("--message-measure");
    expect(css).toContain("--dock-measure");
    expect(css).toContain(".theme-root .welcome.command-deck");
    expect(css).toContain(".theme-root .recent-panel-header");
    expect(css).toContain(".theme-root .workspace-layout");
    expect(css).toContain(".theme-root .chat-activity-stream");
    expect(css).toContain(".theme-root .terminal-panel.expanded .terminal-surface");
    expect(css).toContain(".theme-root .inspector-tabs");
    expect(css).toContain(".theme-root .event-item");
  });

  it("prevents collapsed terminal surfaces from stealing composer input focus", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain(".theme-root .terminal-panel.collapsed .terminal-surface");
    expect(css).toContain("pointer-events: none");
    expect(css).toContain(".theme-root .terminal-panel.expanded .terminal-surface");
    expect(css).toContain("pointer-events: auto");
  });
});
