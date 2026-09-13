"""把 ``never stop early`` 的 Loop termination discipline 固化为代码。

The SOP stops on the first of these conditions, and the exhaustion signal is
always measured against VANILLA (the fixed cold-start baseline) on train, never
against the previous parent and never against the sealed test set:

- ``patience`` consecutive rounds in which no candidate beat vanilla on train
  (exploration exhausted — the primary signal), or
- ``max_rounds`` reached (a hard cap backstop), or
- ``max_consecutive_errors`` rounds in a row that produced no real decision
  (failed or inconclusive evidence). A no-decision round is NOT evidence about
  exploration, so it must not burn patience — but an endless outage must not
  loop either, so it gets its own counter and an honest ``errors_exhausted``
  stop reason.

``record_round(promoted=...)`` 接收 vanilla-comparison signal：本轮至少一个 candidate full-train
confirm beat vanilla 才为 True，不管它是否同时 beat ratcheted parent 并 bank。small unit-tested
tracker 让 weak driver 也能运行 Loop；stop decision 属于 Harness，不依赖 model 记忆。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


class CampaignBudgetExceeded(RuntimeError):  # noqa: N818 — public compatibility name
    """A campaign resource cap was reached; callers must stop fail-closed."""


@dataclass(frozen=True)
class CampaignBudget:
    """Run-wide hard caps, independent of round patience."""

    max_candidates: int | None = None
    max_evaluations: int | None = None
    max_llm_calls: int | None = None
    max_driver_tokens: int | None = None
    max_wall_time_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_candidates",
            "max_evaluations",
            "max_llm_calls",
            "max_driver_tokens",
        ):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be >= 1 when configured")
        if self.max_wall_time_seconds is not None and self.max_wall_time_seconds <= 0:
            raise ValueError("max_wall_time_seconds must be > 0 when configured")


@dataclass(frozen=True)
class CampaignUsage:
    candidates: int = 0
    evaluations: int = 0
    llm_calls: int = 0
    driver_tokens: int = 0
    wall_time_seconds: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "candidates": self.candidates,
            "evaluations": self.evaluations,
            "llm_calls": self.llm_calls,
            "driver_tokens": self.driver_tokens,
            "wall_time_seconds": self.wall_time_seconds,
        }


class CampaignBudgetTracker:
    """Atomically account for campaign resources and stop before an overrun."""

    def __init__(self, budget: CampaignBudget, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.budget = budget
        self._clock = clock
        self._started = clock()
        self.usage = CampaignUsage()

    def consume(
        self,
        *,
        candidates: int = 0,
        evaluations: int = 0,
        llm_calls: int = 0,
        driver_tokens: int = 0,
    ) -> CampaignUsage:
        amounts = {
            "candidates": candidates,
            "evaluations": evaluations,
            "llm_calls": llm_calls,
            "driver_tokens": driver_tokens,
        }
        if any(type(value) is not int or value < 0 for value in amounts.values()):
            raise ValueError("campaign usage increments must be non-negative integers")
        elapsed = max(0.0, self._clock() - self._started)
        projected = CampaignUsage(
            candidates=self.usage.candidates + candidates,
            evaluations=self.usage.evaluations + evaluations,
            llm_calls=self.usage.llm_calls + llm_calls,
            driver_tokens=self.usage.driver_tokens + driver_tokens,
            wall_time_seconds=elapsed,
        )
        reason = self._exceeded_reason(projected)
        if reason is not None:
            raise CampaignBudgetExceeded(reason)
        self.usage = projected
        return self.usage

    def check(self) -> tuple[bool, str | None]:
        elapsed = max(0.0, self._clock() - self._started)
        current = CampaignUsage(**{**self.usage.to_dict(), "wall_time_seconds": elapsed})
        reason = self._exceeded_reason(current)
        return reason is not None, reason

    def _exceeded_reason(self, usage: CampaignUsage) -> str | None:
        caps = (
            ("max_candidates", usage.candidates),
            ("max_evaluations", usage.evaluations),
            ("max_llm_calls", usage.llm_calls),
            ("max_driver_tokens", usage.driver_tokens),
            ("max_wall_time_seconds", usage.wall_time_seconds),
        )
        for name, value in caps:
            limit = getattr(self.budget, name)
            if limit is not None and value >= limit:
                return f"campaign budget exhausted: {name}={value} limit={limit}"
        return None


@dataclass
class TerminationTracker:
    """跨 round 跟踪 promotion/no-decision counter，并决定是否停止。

    tracker 是进程内状态，resume 时应由 journal replay 恢复；它不读取 sealed test。
    """

    patience: int = 10
    max_rounds: int = 20
    max_consecutive_errors: int = 5
    rounds_completed: int = 0
    consecutive_no_promotion: int = 0
    consecutive_errors: int = 0

    def __post_init__(self) -> None:
        if self.patience < 1:
            raise ValueError("patience must be >= 1")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")

    def record_round(self, promoted: bool, *, errored: bool = False) -> None:
        """记录一个 completed round outcome。

        ``promoted`` 是 SOP exhaustion signal：至少一个 candidate full-train beat VANILLA。
        ``errored`` 表示全部 candidate failed/inconclusive、无真实 decision；它增加 round/error
        counter，但保持 patience untouched。正常 decision 会 reset consecutive_errors。
        """
        self.rounds_completed += 1
        if errored:
            self.consecutive_errors += 1
            return
        self.consecutive_errors = 0
        if promoted:
            self.consecutive_no_promotion = 0
        else:
            self.consecutive_no_promotion += 1

    def should_stop(self) -> tuple[bool, str | None]:
        """根据已记录 round 返回 ``(stop, reason)``。

        continue 时 reason 为 ``None``。检查顺序为 max_rounds、errors_exhausted、
        patience_exhausted，因此同轮同时到 cap/patience 时报告 hard cap。返回 stop 不是 run
        finalize/unseal 已完成的证明。
        """
        if self.rounds_completed >= self.max_rounds:
            return True, "max_rounds"
        if self.consecutive_errors >= self.max_consecutive_errors:
            return True, "errors_exhausted"
        if self.consecutive_no_promotion >= self.patience:
            return True, "patience_exhausted"
        return False, None


__all__ = [
    "CampaignBudget",
    "CampaignBudgetExceeded",
    "CampaignBudgetTracker",
    "CampaignUsage",
    "TerminationTracker",
]
