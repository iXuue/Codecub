import type { I18nKey } from "../i18n";
import type { PendingApproval } from "../state/approvalState";

type ApprovalDialogProps = {
  t: (key: I18nKey) => string;
  approvals: PendingApproval[];
  onApprove: (approvalId: string, runId: string) => void;
  onReject: (approvalId: string, runId: string) => void;
};

export function ApprovalDialog({ t, approvals, onApprove, onReject }: ApprovalDialogProps) {
  if (approvals.length === 0) {
    return null;
  }

  return (
    <section className="approval-panel" aria-label={t("approvalRequired")}>
      <div className="panel-title">{t("approvalRequired")}</div>
      {approvals.map((approval) => (
        <div className="approval-item" key={approval.approvalId}>
          <div className="approval-title">
            <strong>{approval.toolName}</strong>
            <span>{approval.riskLevel || t("riskLevel")}</span>
          </div>
          {approval.args.path ? <Row label="path" value={String(approval.args.path)} /> : null}
          {approval.args.command ? <Row label="command" value={String(approval.args.command)} /> : null}
          {approval.cwd ? <Row label={t("cwd")} value={approval.cwd} /> : null}
          {approval.timeout !== null ? <Row label={t("timeout")} value={`${approval.timeout}s`} /> : null}
          <div className="approval-actions">
            <button
              className="button primary"
              type="button"
              disabled={approval.resolving}
              onClick={() => onApprove(approval.approvalId, approval.runId)}
            >
              {approval.resolving ? t("approvalResolving") : t("approve")}
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={approval.resolving}
              onClick={() => onReject(approval.approvalId, approval.runId)}
            >
              {t("reject")}
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="approval-row">
      <span>{label}</span>
      <code>{value}</code>
    </div>
  );
}
