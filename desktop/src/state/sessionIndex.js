export function upsertRecentProject(items, project) {
    return [project, ...items.filter((item) => item.path !== project.path)].slice(0, 20);
}
