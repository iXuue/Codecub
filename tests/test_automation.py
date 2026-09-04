from datetime import datetime, timezone

import pytest

from codecub.automation import AutomationScheduler, CronScheduleError, CronStore, cron_next
from codecub.spine import Origin


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_cron_next_supports_lists_ranges_and_steps():
    assert cron_next("*/15 12 * * *", NOW).isoformat() == "2026-01-01T12:15:00+00:00"
    assert cron_next("0 9 1-5 1,2 0-4", NOW).isoformat() == "2026-01-02T09:00:00+00:00"


def test_scheduler_persists_and_submits_due_at_and_every_jobs(tmp_path):
    clock_value = [NOW]
    submitted = []
    scheduler = AutomationScheduler(
        CronStore(tmp_path / "jobs.json"),
        submitted.append,
        clock=lambda: clock_value[0],
    )
    scheduler.create({"id": "once", "session_id": "s1", "message": "one", "at": "2026-01-01T12:00:00Z"})
    scheduler.create({"id": "repeat", "session_id": "s1", "message": "many", "every": "10s"})

    first = scheduler.tick()
    assert [item["job_id"] for item in first] == ["once"]
    assert submitted[0].origin is Origin.CRON
    assert submitted[0].source.channel == "cron"
    assert scheduler.tick() == []

    clock_value[0] = NOW.replace(second=10)
    second = scheduler.tick()
    assert [item["job_id"] for item in second] == ["repeat"]
    assert len(submitted) == 2

    restored = AutomationScheduler(CronStore(tmp_path / "jobs.json"), submitted.append, clock=lambda: clock_value[0])
    states = {item["id"]: item for item in restored.list()}
    assert states["once"]["enabled"] is False
    assert states["repeat"]["enabled"] is True


def test_scheduler_rejects_invalid_cron_and_duplicate_jobs(tmp_path):
    scheduler = AutomationScheduler(CronStore(tmp_path / "jobs.json"), lambda _request: None, clock=lambda: NOW)
    with pytest.raises(CronScheduleError):
        scheduler.create({"id": "bad", "session_id": "s", "message": "x", "cron": "every hour"})
    scheduler.create({"id": "same", "session_id": "s", "message": "x", "every": 60})
    with pytest.raises(CronScheduleError, match="already exists"):
        scheduler.create({"id": "same", "session_id": "s", "message": "x", "every": 60})
