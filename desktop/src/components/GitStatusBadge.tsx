import { useEffect, useState } from "react";
import type { I18nKey } from "../i18n";

type GitStatus = {
  branch: string;
  dirty: boolean;
  changedCount: number;
  ahead: number;
  behind: number;
  files: string[];
};

type GitStatusBadgeProps = {
  t: (key: I18nKey) => string;
  projectPath: string;
};

export function GitStatusBadge({ t, projectPath }: GitStatusBadgeProps) {
  const [status, setStatus] = useState<GitStatus | null>(null);

  async function refresh() {
    setStatus(await window.codecub.loadGitStatus(projectPath));
  }

  useEffect(() => {
    void refresh();
  }, [projectPath]);

  return (
    <button className={status?.dirty ? "git-badge dirty" : "git-badge"} type="button" onClick={refresh}>
      <span>{t("git")}</span>
      <strong>{status?.branch ?? "-"}</strong>
      <span>{status?.dirty ? `${status.changedCount} changed` : t("clean")}</span>
    </button>
  );
}
