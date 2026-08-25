import { useRef, useState } from "react";
import type { I18nKey } from "../i18n";
import type { ApprovalState } from "../state/approvalState";
import type { BackendEvent } from "../state/backendEvents";
import { ApprovalDialog } from "./ApprovalDialog";
import { DiffPreviewPanel } from "./DiffPreviewPanel";
import { RunLogSidebar } from "./RunLogSidebar";
import { UsagePanel } from "./UsagePanel";
import type { UsageState } from "../state/usageState";
import { gsap, motionAllowed, useGSAP } from "../motion/gsapSetup";

type RunInspectorPanelProps = {
  t: (key: I18nKey) => string;
  events: BackendEvent[];
  approvalState: ApprovalState;
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
  usageState: UsageState;
};

export function RunInspectorPanel({
  t,
  events,
  approvalState,
  onApprove,
  onReject,
  usageState,
}: RunInspectorPanelProps) {
  const [activeTab, setActiveTab] = useState<"changes" | "logs" | "usage" | "approvals">("logs");
  const tabBodyRef = useRef<HTMLDivElement | null>(null);

  useGSAP(
    () => {
      if (!motionAllowed()) {
        return;
      }
      gsap.fromTo(
        ".inspector-tab-body",
        { autoAlpha: 0, y: 8 },
        { autoAlpha: 1, y: 0, duration: 0.22, ease: "power2.out" },
      );
    },
    { dependencies: [activeTab], scope: tabBodyRef, revertOnUpdate: true },
  );

  return (
    <aside className="run-inspector workspace-column" aria-label={t("runInspector")}>
      <div className="inspector-tabs" role="tablist" aria-label={t("runInspectorTabs")}>
        <button
          className={activeTab === "usage" ? "inspector-tab active" : "inspector-tab"}
          type="button"
          role="tab"
          aria-selected={activeTab === "usage"}
          onClick={() => setActiveTab("usage")}
        >
          {t("usage")}
        </button>
        <button
          className={activeTab === "changes" ? "inspector-tab active" : "inspector-tab"}
          type="button"
          role="tab"
          aria-selected={activeTab === "changes"}
          onClick={() => setActiveTab("changes")}
        >
          {t("changes")}
        </button>
        <button
          className={activeTab === "logs" ? "inspector-tab active" : "inspector-tab"}
          type="button"
          role="tab"
          aria-selected={activeTab === "logs"}
          onClick={() => setActiveTab("logs")}
        >
          {t("logs")}
        </button>
        <button
          className={activeTab === "approvals" ? "inspector-tab active" : "inspector-tab"}
          type="button"
          role="tab"
          aria-selected={activeTab === "approvals"}
          onClick={() => setActiveTab("approvals")}
        >
          {t("approvals")}
        </button>
      </div>
      <div className="inspector-tab-shell" ref={tabBodyRef}>
        <div className="inspector-tab-body">
          {activeTab === "changes" ? <DiffPreviewPanel t={t} events={events} /> : null}
          {activeTab === "logs" ? <RunLogSidebar t={t} events={events} /> : null}
          {activeTab === "usage" ? <UsagePanel t={t} usageState={usageState} /> : null}
          {activeTab === "approvals" ? (
            <ApprovalDialog t={t} approvals={approvalState.pending} onApprove={onApprove} onReject={onReject} />
          ) : null}
        </div>
      </div>
    </aside>
  );
}
