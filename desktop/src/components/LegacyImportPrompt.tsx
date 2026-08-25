import { useState } from "react";
import type { I18nKey } from "../i18n";
import type { BackendEvent } from "../state/backendEvents";

type LegacyImportPromptProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
  onImport: () => void;
};

export function LegacyImportPrompt({ t, events, onImport }: LegacyImportPromptProps) {
  const [dismissed, setDismissed] = useState(false);
  const detected = [...events].reverse().find((event) => event.type === "legacy_import_detected");
  const completed = [...events].reverse().find((event) => event.type === "legacy_import_completed");
  const failed = [...events].reverse().find((event) => event.type === "legacy_import_failed");

  if (completed) {
    return (
      <section className="legacy-import result">
        <strong>{t("legacyImportTitle")}</strong>
        <span>
          {t("imported")}: {String(completed.payload.imported_count ?? 0)} · {t("skipped")}:{" "}
          {String(completed.payload.skipped_count ?? 0)}
        </span>
      </section>
    );
  }

  if (failed) {
    return (
      <section className="legacy-import result error">
        <strong>{t("legacyImportTitle")}</strong>
        <span>{String(failed.payload.message ?? "")}</span>
      </section>
    );
  }

  if (!detected || dismissed || Number(detected.payload.session_count ?? 0) <= 0) {
    return null;
  }

  return (
    <section className="legacy-import">
      <div>
        <strong>{t("legacyImportTitle")}</strong>
        <span>
          {t("legacyImportBody")} ({String(detected.payload.session_count ?? 0)})
        </span>
      </div>
      <div className="legacy-actions">
        <button className="button primary" type="button" onClick={onImport}>
          {t("importLegacy")}
        </button>
        <button className="button secondary" type="button" onClick={() => setDismissed(true)}>
          {t("dismiss")}
        </button>
      </div>
    </section>
  );
}
