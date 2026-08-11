import { describe, expect, it } from "vitest";
import { parseProjectEnvText, updateProjectEnvText } from "../electron/projectEnv";

describe("legacy project env helpers", () => {
  it("parses quoted values without exposing project env as active model settings", () => {
    const original = [
      "# existing config",
      'OPENAI_API_KEY="sk old # keep"',
      "OTHER_VALUE=preserve-me",
      "",
    ].join("\n");

    expect(parseProjectEnvText(original)).toEqual({
      OPENAI_API_KEY: "sk old # keep",
      OTHER_VALUE: "preserve-me",
    });
  });

  it("can update env text for legacy maintenance without being used by the settings UI", () => {
    const original = [
      "# existing config",
      'OPENAI_API_KEY="sk old # keep"',
      "OTHER_VALUE=preserve-me",
      "",
    ].join("\n");

    const updated = updateProjectEnvText(original, {
      CODECUB_PROVIDER: "openai",
      OPENAI_MODEL: "qwen flash",
      OPENAI_API_KEY: null,
    });

    expect(updated).toContain("# existing config");
    expect(updated).toContain("OTHER_VALUE=preserve-me");
    expect(updated).toContain('OPENAI_MODEL="qwen flash"');
    expect(updated).toContain("CODECUB_PROVIDER=openai");
    expect(updated).not.toContain("OPENAI_API_KEY");
  });
});
