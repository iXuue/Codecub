import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunTrail } from "../src/components/RunTrail";
import { t } from "../src/i18n";

describe("RunTrail", () => {
  it("renders every high-level run step", () => {
    render(
      <RunTrail
        t={(key) => t("en-US", key)}
        steps={[
          { id: "context", state: "complete" },
          { id: "model", state: "active" },
          { id: "tool", state: "pending" },
          { id: "diff", state: "pending" },
          { id: "done", state: "pending" },
        ]}
      />,
    );
    expect(screen.getByText("Context")).toBeTruthy();
    expect(screen.getByText("Model")).toBeTruthy();
    expect(screen.getByText("Tool")).toBeTruthy();
    expect(screen.getByText("Diff")).toBeTruthy();
    expect(screen.getByText("Done")).toBeTruthy();
  });
});
