"""Turn lifecycle boundary; conversation scheduling remains in Spine."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoopOutcome:
    """A terminal fact produced by the legacy model/tool loop.

    It deliberately contains no persistence or event behaviour.  TurnRunner
    owns mapping this fact to the run lifecycle.
    """

    answer: str
    kind: str
    task_state: object
    user_message: str
    started_at: float
    started_wall: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TurnPreparation:
    """Identity and state established before the legacy loop starts."""

    task_state: object
    started_at: float
    started_wall: str


class TurnRunner:
    def __init__(self, lifecycle, loop, hooks):
        self.lifecycle = lifecycle
        self.loop = loop
        self.hooks = hooks

    def run(self, user_message, run_id=""):
        subject = getattr(self.lifecycle, "hook_subject", self.lifecycle)
        self.hooks.before_turn(subject, user_message=user_message, run_id=run_id)
        try:
            preparation = self.lifecycle.initialize(user_message, run_id)
            bind_loop = getattr(self.lifecycle, "bind_loop", None)
            if bind_loop is not None:
                bind_loop(self.loop)
            outcome = self.loop.run(user_message, run_id=run_id, preparation=preparation)
        except Exception as exc:
            self.hooks.on_error(subject, error=exc, run_id=run_id)
            raise
        if isinstance(outcome, LoopOutcome):
            answer = self.finalize(outcome)
            if outcome.kind == "cancelled":
                self.hooks.on_cancel(subject, run_id=outcome.task_state.run_id)
        else:
            # Transitional compatibility for loops not yet converted to
            # LoopOutcome.  It will disappear with the remaining branches.
            answer = outcome
            if hasattr(self.lifecycle, "hook_subject"):
                cancelled = self.lifecycle.cancellation_requested()
            else:
                state = getattr(self.lifecycle, "current_task_state", None)
                cancelled = bool(state and self.lifecycle.cancellation_requested(state))
            if cancelled:
                self.hooks.on_cancel(subject, run_id=run_id)
        self.hooks.after_turn(subject, answer=answer, run_id=run_id)
        return answer

    def finalize(self, outcome):
        """Persist one terminal outcome exactly once, after the loop has stopped."""
        task_state = outcome.task_state
        kind = outcome.kind
        if kind == "success":
            self.lifecycle.emit_status(task_state, "finalizing", "Finalizing response", outcome)
            self.lifecycle.record_assistant(outcome.answer)
            task_state.finish_success(outcome.answer)
            self.lifecycle.enrich_memory(outcome)
            self._checkpoint(task_state, outcome, "run_finished")
            self.lifecycle.write_task_state(task_state)
            self.lifecycle.emit_checkpoint_created(task_state, "run_finished")
            self.lifecycle.emit_run_finished(task_state, outcome)
            self.lifecycle.emit_status(task_state, "completed", "Completed", outcome)
        elif kind == "limited":
            if outcome.metadata.get("retry_limit"):
                task_state.stop_retry_limit(outcome.answer)
            else:
                task_state.stop_step_limit(outcome.answer)
            self._finalize_stopped(outcome, "failed", "Failed")
        elif kind == "model_error":
            self.lifecycle.record_model_error(outcome)
            task_state.stop_model_error(outcome.answer)
            self.lifecycle.write_task_state(task_state)
            self.lifecycle.emit_model_error(task_state, outcome)
            self.lifecycle.emit_status(task_state, "failed", "Failed", outcome, "model_error")
            self.lifecycle.emit_run_finished(task_state, outcome)
        elif kind == "cancelled":
            task_state.stop_user_canceled(outcome.answer)
            self.lifecycle.write_task_state(task_state)
            self.lifecycle.emit_cancelled(task_state, outcome)
            self.lifecycle.emit_status(task_state, "canceled", "Canceled", outcome)
        elif kind == "finalization_failed":
            task_state.stop("finalization_failed", final_answer=outcome.answer)
            self._finalize_stopped(outcome, "failed", "Failed", checkpoint=False)
        elif kind == "stuck":
            task_state.stop("stuck_confirmed", final_answer=outcome.answer)
            self._finalize_stopped(outcome, "failed", "Stopped")
        elif kind == "emergency_cap":
            task_state.stop("emergency_cap_reached", final_answer=outcome.answer)
            self.lifecycle.emit_emergency_cap(task_state, outcome)
            self._finalize_stopped(outcome, "failed", "Stopped")
        else:
            raise ValueError(f"unsupported loop outcome: {kind}")
        self.lifecycle.write_final_report(task_state)
        return outcome.answer

    def _finalize_stopped(self, outcome, phase, label, checkpoint=True):
        task_state = outcome.task_state
        self.lifecycle.record_assistant(outcome.answer)
        self.lifecycle.enrich_memory(outcome)
        self.lifecycle.write_task_state(task_state)
        if checkpoint:
            trigger = task_state.stop_reason or "run_stopped"
            self._checkpoint(task_state, outcome, trigger)
            self.lifecycle.emit_checkpoint_created(task_state, trigger)
        self.lifecycle.emit_run_finished(task_state, outcome)
        self.lifecycle.emit_status(task_state, phase, label, outcome, task_state.stop_reason)

    def _checkpoint(self, task_state, outcome, trigger):
        self.lifecycle.create_checkpoint(task_state, outcome.user_message, trigger)
