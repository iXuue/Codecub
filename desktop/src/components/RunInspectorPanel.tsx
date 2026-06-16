import type { I18nKey } from "../i18n";
import type { ApprovalState } from "../state/approvalState";
import type { BackendEvent } from "../state/backendEvents";
import type { ChatState } from "../state/chatState";
import { deriveRunTrail } from "../state/runTrailState";
import { ApprovalDialog } from "./ApprovalDialog";
import { CubStatusChip } from "./CubStatusChip";
import { DiffPreviewPanel } from "./DiffPreviewPanel";
import { RunLogSidebar } from "./RunLogSidebar";
import { RunTrail } from "./RunTrail";

type RunInspectorPanelProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
  chatState: ChatState;
  approvalState: ApprovalState;
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
};

export function RunInspectorPanel({
  t,
  events,
  chatState,
  approvalState,
  onApprove,
  onReject,
}: RunInspectorPanelProps) {
  return (
    <aside className="run-inspector" aria-label={t("runInspector")}>
      <section className="inspector-section">
        <CubStatusChip t={t} chatState={chatState} />
        <RunTrail t={t} steps={deriveRunTrail(chatState, events)} />
      </section>
      <ApprovalDialog t={t} approvals={approvalState.pending} onApprove={onApprove} onReject={onReject} />
      <DiffPreviewPanel t={t} events={events} />
      <RunLogSidebar t={t} events={events} />
    </aside>
  );
}
