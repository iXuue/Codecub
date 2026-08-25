import { mkdir, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { installProjectExtension, listProjectExtensions } from "../electron/projectExtensions";

async function createSkill(path: string, name = "Local Skill") {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "SKILL.md"), `---\nname: ${name}\n---\n# ${name}\n`, "utf-8");
}

async function createPlugin(path: string, name = "Local Plugin") {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "plugin.json"), JSON.stringify({ name }), "utf-8");
}

describe("projectExtensions", () => {
  it("lists installed skills and plugins", async ({ task }) => {
    const projectPath = join(process.cwd(), ".tmp", task.id, randomUUID(), "project");
    await createSkill(join(projectPath, ".codecub", "skills", "my-skill"), "My Skill");
    await createPlugin(join(projectPath, ".codecub", "plugins", "my-plugin"), "My Plugin");

    const extensions = await listProjectExtensions(projectPath);

    expect(extensions.skills[0]).toMatchObject({ id: "my-skill", kind: "skill", name: "My Skill" });
    expect(extensions.plugins[0]).toMatchObject({ id: "my-plugin", kind: "plugin", name: "My Plugin" });
  });

  it("installs a local skill folder into the project", async ({ task }) => {
    const root = join(process.cwd(), ".tmp", task.id, randomUUID());
    const projectPath = join(root, "project");
    const sourcePath = join(root, "My Skill");
    await createSkill(sourcePath, "Copied Skill");

    const result = await installProjectExtension(projectPath, sourcePath, "skill");

    expect(result.error).toBeUndefined();
    expect(result.extension).toMatchObject({ id: "my-skill", kind: "skill", name: "Copied Skill" });
    const extensions = await listProjectExtensions(projectPath);
    expect(extensions.skills.map((skill) => skill.id)).toEqual(["my-skill"]);
  });

  it("fails when a plugin source is missing plugin.json", async ({ task }) => {
    const root = join(process.cwd(), ".tmp", task.id, randomUUID());
    const projectPath = join(root, "project");
    const sourcePath = join(root, "Missing Plugin");
    await mkdir(sourcePath, { recursive: true });

    const result = await installProjectExtension(projectPath, sourcePath, "plugin");

    expect(result.error).toMatch(/plugin\.json|ENOENT/);
  });

  it("fails rather than overwriting an installed extension", async ({ task }) => {
    const root = join(process.cwd(), ".tmp", task.id, randomUUID());
    const projectPath = join(root, "project");
    const sourcePath = join(root, "Existing Skill");
    await createSkill(sourcePath);
    await installProjectExtension(projectPath, sourcePath, "skill");

    const duplicate = await installProjectExtension(projectPath, sourcePath, "skill");

    expect(duplicate.error).toContain("already installed");
  });
});
