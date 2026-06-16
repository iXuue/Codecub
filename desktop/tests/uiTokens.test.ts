import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("UI tokens", () => {
  it("defines the P0.9 Quiet Cub Workbench token baseline", () => {
    const css = readFileSync(join(process.cwd(), "src", "styles", "app.css"), "utf-8");
    expect(css).toContain("--color-bg: #F6F8FA");
    expect(css).toContain("--color-brand: #2F6F73");
    expect(css).toContain("--color-accent: #D6A84F");
    expect(css).toContain("--color-code-surface: #111820");
    expect(css).toContain("--color-glass-surface: rgba(255, 255, 255, 0.72)");
    expect(css).toContain("--color-glass-border: rgba(217, 224, 231, 0.72)");
    expect(css).toContain("--motion-fast: 120ms");
    expect(css).toContain("--motion-base: 180ms");
    expect(css).toContain("--radius-panel: 8px");
    expect(css).toContain("--font-code:");
  });
});
