import type { I18nKey } from "../i18n";
import type { RunTrailStep, RunTrailStepId } from "../state/runTrailState";

type RunTrailProps = {
  t: (key: I18nKey) => string;
  steps: RunTrailStep[];
};

const labels: Record<RunTrailStepId, I18nKey> = {
  context: "trailContext",
  model: "trailModel",
  tool: "trailTool",
  diff: "trailDiff",
  done: "trailDone",
};

export function RunTrail({ t, steps }: RunTrailProps) {
  return (
    <ol className="run-trail" aria-label={t("runTrail")}>
      {steps.map((step) => (
        <li className={`run-trail-step ${step.state}`} key={step.id}>
          {t(labels[step.id])}
        </li>
      ))}
    </ol>
  );
}
