"""Догон пропущенных напоминаний: окно, однократность, выключение (R-47)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.config import Settings
from src.core.schedule import Weekday, make_once_draft, make_weekly_draft
from src.core.timeplan import past_moment_in_window, past_weekly_moment
from src.db import repository
from src.scheduler.multi import JobFactories, MultiUserJobs
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeSender

ZONE = ZoneInfo("UTC")
# Среда 04.11.2026, 19:30 — время тренировки.
WEDNESDAY = date(2026, 11, 4)
MOMENT = datetime(2026, 11, 4, 19, 30, tzinfo=UTC)
LATE = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)
TOO_LATE = datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
WINDOW = timedelta(hours=2)


def _workout(session_factory, at=time(19, 30), weekday=Weekday.WEDNESDAY) -> int:
    with session_factory() as session:
        return repository.create_workout(
            session, make_weekly_draft("бег", (weekday,), at)
        ).id


def _once(session_factory, day=WEDNESDAY, at=time(19, 30)) -> int:
    with session_factory() as session:
        return repository.create_workout(
            session, make_once_draft("бег", day, at)
        ).id


# --- Окно -----------------------------------------------------------------


def test_past_moment_within_window():
    assert past_weekly_moment(LATE, ZONE, Weekday.WEDNESDAY, time(19, 30), WINDOW) == MOMENT


def test_past_moment_outside_window():
    assert past_weekly_moment(TOO_LATE, ZONE, Weekday.WEDNESDAY, time(19, 30), WINDOW) is None


def test_window_crosses_midnight():
    # 23:30 вчера, сейчас 00:20 — окно 2 часа.
    now = datetime(2026, 11, 5, 0, 20, tzinfo=UTC)
    monday = date(2026, 11, 4)

    moment = past_moment_in_window(now, ZONE, monday, time(23, 30), WINDOW)

    assert moment == datetime(2026, 11, 4, 23, 30, tzinfo=UTC)


def test_other_weekday_is_not_caught():
    assert past_weekly_moment(LATE, ZONE, Weekday.MONDAY, time(19, 30), WINDOW) is None


# --- Тик ------------------------------------------------------------------


async def test_late_reminder_is_sent_once(session_factory):
    workout_id = _workout(session_factory)
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE, catchup_window=WINDOW)

    first = await tick.run(LATE)
    second = await tick.run(LATE)

    assert len(first.claimed) == 1
    assert len(sender.sent) == 1
    assert sender.sent[0].workout_id == workout_id
    assert sender.sent[0].scheduled_at_utc == MOMENT
    assert second.claimed == []


async def test_too_late_is_not_sent(session_factory):
    _workout(session_factory)
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE, catchup_window=WINDOW)

    result = await tick.run(TOO_LATE)

    assert result.claimed == []
    assert sender.sent == []


async def test_catchup_disabled(session_factory):
    _workout(session_factory)
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE, catchup_window=None)

    await tick.run(LATE)

    assert sender.sent == []


async def test_once_workout_is_caught_up(session_factory):
    workout_id = _once(session_factory)
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE, catchup_window=WINDOW)

    await tick.run(LATE)

    assert [message.workout_id for message in sender.sent] == [workout_id]


async def test_normal_window_still_works(session_factory):
    _workout(session_factory)
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE, catchup_window=WINDOW)

    await tick.run(MOMENT)

    assert len(sender.sent) == 1


# --- Несколько пользователей ----------------------------------------------


def _register(session_factory, telegram_id: int) -> None:
    with session_factory() as session:
        repository.create_user(session, telegram_id, "UTC", "21:00", "20:00")


async def test_catchup_for_two_users(session_factory):
    from src.db.context import acting_as

    _register(session_factory, 555)
    with session_factory() as session, acting_as(1):
        repository.create_workout(
            session, make_weekly_draft("бег", (Weekday.WEDNESDAY,), time(19, 30))
        )
    with session_factory() as session, acting_as(2):
        repository.create_workout(
            session, make_weekly_draft("силовая", (Weekday.WEDNESDAY,), time(19, 30))
        )
    senders: dict[int, FakeSender] = {}
    factories = JobFactories(
        reminder_sender=lambda user: senders.setdefault(user.id, FakeSender()),
        wellness_sender=lambda user: pytest.fail("не должен вызываться"),
        report_sender=lambda user: pytest.fail("не должен вызываться"),
        render_weekly=lambda user, now: "",
    )
    jobs = MultiUserJobs(session_factory, factories, catchup_window=WINDOW)

    await jobs.run_reminders(LATE)

    assert len(senders[1].sent) == 1
    assert len(senders[2].sent) == 1


# --- Настройка -------------------------------------------------------------


def make_settings(**overrides):
    values = {
        "bot_token": "123:TEST",
        "owner_id": 42,
        "tz": "UTC",
        "db_url": "sqlite:///test.db",
    }
    values.update(overrides)
    return Settings(**values)


def test_catchup_default_and_off():
    assert make_settings().catchup_hours == 2
    assert make_settings(catchup_hours=0).catchup_hours == 0


def test_catchup_negative_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="CATCHUP_HOURS"):
        make_settings(catchup_hours=-1)
