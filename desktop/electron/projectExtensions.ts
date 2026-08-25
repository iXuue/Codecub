import { cp, mkdir, readdir, readFile, stat } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import type { ExtensionKind, InstallProjectExtensionResult, ProjectExtension, ProjectExtensions } from "./ipcTypes.js";

const SKIP_DIRECTORIES = new Set([".git", "node_modules", "__pycache__"]);

export async function listProjectExtensions(projectPath: string): Promise<ProjectExtensions> {
  const [skills, plugins] = await Promise.all([
    listExtensionKind(projectPath, "skill"),
    listExtensionKind(projectPath, "plugin"),
  ]);
  return { skills, plugins };
}

export async function installProjectExtension(
  projectPath: string,
  sourcePath: string,
  kind: ExtensionKind,
): Promise<InstallProjectExtensionResult> {
  try {
    const id = normalizeExtensionId(basename(sourcePath));
    if (!id) {
      return { canceled: false, error: "Extension folder name cannot be converted to a safe id." };
    }

    const sourceManifestPath = manifestPath(sourcePath, kind);
    await stat(sourceManifestPath);

    const destinationRoot = extensionRoot(projectPath, kind);
    const destinationPath = join(destinationRoot, id);
    if (await exists(destinationPath)) {
      return { canceled: false, error: `Extension '${id}' is already installed.` };
    }

    await mkdir(destinationRoot, { recursive: true });
    await cp(sourcePath, destinationPath, {
      recursive: true,
      filter: (source) => !SKIP_DIRECTORIES.has(basename(source)),
    });

    return {
      canceled: false,
      extension: await readExtension(destinationPath, id, kind),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { canceled: false, error: message };
  }
}

async function listExtensionKind(projectPath: string, kind: ExtensionKind): Promise<ProjectExtension[]> {
  const root = extensionRoot(projectPath, kind);
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }

  const extensions = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory())
      .map(async (entry) => readExtension(join(root, entry.name), entry.name, kind).catch(() => null)),
  );
  return extensions
    .filter((extension): extension is ProjectExtension => extension !== null)
    .sort((left, right) => left.id.localeCompare(right.id));
}

async function readExtension(path: string, id: string, kind: ExtensionKind): Promise<ProjectExtension> {
  const manifest = await readManifest(path, kind);
  const fileStat = await stat(path);
  return {
    id,
    kind,
    name: manifest.name || id,
    path: resolve(path),
    installedAt: fileStat.mtime.toISOString(),
  };
}

async function readManifest(path: string, kind: ExtensionKind): Promise<{ name: string }> {
  if (kind === "plugin") {
    const parsed = JSON.parse(await readFile(manifestPath(path, kind), "utf-8"));
    return {
      name: stringValue(parsed?.displayName) || stringValue(parsed?.name),
    };
  }

  const skillText = await readFile(manifestPath(path, kind), "utf-8");
  const frontmatterName = skillText.match(/^name:\s*["']?([^"'\r\n]+)["']?\s*$/m)?.[1]?.trim() ?? "";
  const titleName = skillText.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? "";
  return { name: frontmatterName || titleName };
}

function extensionRoot(projectPath: string, kind: ExtensionKind): string {
  return join(projectPath, ".codecub", kind === "skill" ? "skills" : "plugins");
}

function manifestPath(path: string, kind: ExtensionKind): string {
  return join(path, kind === "skill" ? "SKILL.md" : "plugin.json");
}

function normalizeExtensionId(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
