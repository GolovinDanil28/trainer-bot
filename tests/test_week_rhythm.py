"""Недельный ритм: дистанция в тексте (R-39, R-07) и авторазбор (R-40)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from src.config import Settings
from src.core.messages import format_plan, format_reminder, format_volume
from src.core.plan import PlanSnapshotView, SnapshotItem, build_plan, build_weekly_review
from src.core.reminders import ReminderMessage
from src.core.schedule import Weekday, WorkoutView
from src.core.wellness import WellnessScore
from src.db import repository
from src.db.models import WeeklyReport
from src.scheduler.runner import WEEKLY_JOB_ID, build_scheduler
from src.scheduler.tick import SchedulerTick
from src.scheduler.weekly_tick import WeeklyTick
from tests.fakes import FakeSender

ZONE = ZoneInfo("UTC")
SUNDAY = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
WEEK_START = date(2026, 10, 26)


class FakeReportSender:
    """Подделка доставки текста: пишет, что отправлено, и умеет падать."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram упал")
        self.sent.append(text)


def _view(distance=7.0, duration=70):
    return WorkoutView(
        id=1,
        workout_type="бег",
        weekdays=(Weekday.TUESDAY,),
        time_of_day=time(8, 0),
        duration_min=duration,
        distance_km=distance,
    )


# --- R-39 / R-07: дистанция в текстах -------------------------------------


@pytest.mark.parametrize(
    ("distance", "duration", "expected"),
    [
        (7, 70, "7 км (≈70 мин)"),
        (5.2, 50, "5.2 км (≈50 мин)"),
        (None, 45, "45 мин"),
    ],
)
def test_volume_text(distance, duration, expected):
    assert format_volume(distance, duration) == expected


def test_plan_shows_distance():
    text = format_plan(build_plan(WellnessScore(5), [_view()]))

    assert "7 км (≈70 мин)" in text


def test_reduced_plan_keeps_distance():
    # R-23 уменьшает минуты, а не километры: 70 → 45, дистанция 7.
    text = format_plan(build_plan(WellnessScore(3), [_view()]))

    assert "— бег, 08:00, 7 км (≈45 мин, обычно 70 мин)" in text


def test_plan_without_distance_shows_only_minutes():
    text = format_plan(build_plan(WellnessScore(5), [_view(distance=None, duration=45)]))

    assert "45 мин" in text
    assert "км" not in text


def _reminder(distance=7.0, duration=70):
    return ReminderMessage(
        reminder_id=1,
        workout_id=1,
        workout_type="бег",
        duration_min=duration,
        scheduled_at_utc=datetime(2026, 11, 3, 8, 0, tzinfo=UTC),
        distance_km=distance,
    )


def test_reminder_shows_distance():
    text = format_reminder(_reminder(), ZONE)

    assert "7 км (≈70 мин)" in text
    assert "08:00" in text


def test_reminder_without_distance():
    text = format_reminder(_reminder(distance=None), ZONE)

    assert "70 мин" in text
    assert "км" not in text


# --- R-40: тик авторазбора -------------------------------------------------


def _weekly_tick(session_factory, sender, render=None):
    return WeeklyTick(
        session_factory,
        sender.send,
        ZONE,
        time(20, 0),
        render or (lambda start, end, _until: f"разбор {start}–{end}"),
    )


async def test_sunday_after_threshold_sends(session_factory):
    sender = FakeReportSender()

    result = await _weekly_tick(session_factory, sender).run(SUNDAY)

    assert result.sent is True
    assert sender.sent == [f"разбор {WEEK_START}–2026-11-01"]


async def test_before_threshold_does_not_send(session_factory):
    sender = FakeReportSender()

    result = await _weekly_tick(session_factory, sender).run(
        datetime(2026, 11, 1, 19, 59, tzinfo=UTC)
    )

    assert result.sent is False
    assert sender.sent == []


async def test_other_day_does_not_send(session_factory):
    sender = FakeReportSender()

    result = await _weekly_tick(session_factory, sender).run(
        datetime(2026, 11, 2, 20, 0, tzinfo=UTC)  # понедельник
    )

    assert result.sent is False
    assert sender.sent == []


async def test_second_tick_and_restart_do_not_duplicate(session_factory):
    sender = FakeReportSender()
    tick = _weekly_tick(session_factory, sender)
    await tick.run(SUNDAY)
    await tick.run(SUNDAY)
    # «Рестарт» — новый экземпляр на той же базе.
    await _weekly_tick(session_factory, sender).run(
        datetime(2026, 11, 1, 21, 0, tzinfo=UTC)
    )

    assert len(sender.sent) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeeklyReport)) == 1


async def test_failed_send_releases_week(session_factory):
    failing = FakeReportSender(fail=True)
    result = await _weekly_tick(session_factory, failing).run(SUNDAY)

    assert result.sent is False
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(WeeklyReport)) == 0

    # Следующий тик пробует снова и добивается успеха.
    healthy = FakeReportSender()
    retry = await _weekly_tick(session_factory, healthy).run(
        datetime(2026, 11, 1, 20, 1, tzinfo=UTC)
    )

    assert retry.sent is True
    assert len(healthy.sent) == 1


async def test_render_receives_week_in_users_timezone(session_factory):
    zone = ZoneInfo("Asia/Yekaterinburg")
    calls: list[tuple[date, date]] = []
    # 20:00 воскресенья по Екатеринбургу = 15:00 UTC.
    moment = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
    tick = WeeklyTick(
        session_factory,
        FakeReportSender().send,
        zone,
        time(20, 0),
        lambda start, end, _until: calls.append((start, end)) or "разбор",
    )

    await tick.run(moment)

    assert calls == [(WEEK_START, date(2026, 11, 1))]


# --- Настройки ------------------------------------------------------------


def make_settings(**overrides):
    values = {
        "bot_token": "123:TEST",
        "owner_id": 42,
        "tz": "UTC",
        "db_url": "sqlite:///test.db",
    }
    values.update(overrides)
    return Settings(**values)


def test_week_review_time_default():
    assert make_settings().week_review_time == "20:00"


def test_week_review_time_normalizes():
    assert make_settings(week_review_time="9:05").week_review_time == "09:05"


@pytest.mark.parametrize("value", ["20-00", "24:00", "20:60", "вечер"])
def test_week_review_time_is_validated(value):
    with pytest.raises(ValidationError, match="WEEK_REVIEW_TIME"):
        make_settings(week_review_time=value)


def test_scheduler_registers_weekly_job(session_factory):
    tick = SchedulerTick(session_factory, FakeSender(), ZONE)
    weekly = _weekly_tick(session_factory, FakeReportSender())

    scheduler = build_scheduler(tick, weekly_tick=weekly)

    assert WEEKLY_JOB_ID in {job.id for job in scheduler.get_jobs()}


# --- День ещё не закончился: будущее не считается пропуском ---------------


def _snapshot_item():
    return PlanSnapshotView(
        plan_date=date(2026, 11, 1),  # воскресенье
        score=4,
        is_rest=False,
        items=(SnapshotItem("бег", time(21, 0), 70, 1,),),
    )


def test_future_item_is_not_counted_as_missed():
    snapshot = _snapshot_item()
    # Разбор в 20:00, тренировка в 21:00 — ещё не наступила.
    until = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)

    review = build_weekly_review(
        [snapshot], [], {}, WEEK_START, date(2026, 11, 1), ZONE, until=until
    )

    assert review.total == 0
    assert review.misses == ()
    assert review.upcoming == 1

    from src.core.messages import format_weekly_review

    text = format_weekly_review(review)
    assert "ещё впереди" in text
    assert "ничего не стояло" not in text


def test_past_item_is_counted_when_until_given():
    snapshot = _snapshot_item()
    until = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)

    review = build_weekly_review(
        [snapshot], [], {}, WEEK_START, date(2026, 11, 1), ZONE, until=until
    )

    assert review.total == 1
    assert review.misses and review.misses[0].reason.value == "no_reminder"


async def test_tick_with_real_service_hides_todays_future_workout(
    session_factory
):
    """Сквозной R-40: тик в 20:00 не объявляет вечернюю тренировку пропуском."""
    from src.bot.service import WorkoutService
    from src.bot.wellness_service import WellnessService
    from src.core.schedule import Weekday

    sunday = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    workouts = WorkoutService(session_factory, ZONE, clock=lambda: sunday)
    workouts.create_weekly("бег", (Weekday.SUNDAY,), time(21, 0), distance_km=7)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: sunday)
    # План на воскресенье показан в субботу вечером — снимок уже есть.
    saturday = datetime(2026, 10, 31, 21, 0, tzinfo=UTC)
    with session_factory() as session:
        repository.save_wellness(session, saturday.date(), 4, None, saturday)
    WellnessService(session_factory, ZONE, clock=lambda: saturday).show_plan(saturday)

    sender = FakeReportSender()
    tick = WeeklyTick(
        session_factory,
        sender.send,
        ZONE,
        time(20, 0),
        lambda start, end, until: wellness.weekly_review_for(start, end, until),
    )

    await tick.run(sunday)

    assert len(sender.sent) == 1
    text = sender.sent[0]
    # Ни «пропущено», ни «в расписании ничего не стояло»: тренировка впереди.
    assert "напоминания не было" not in text
    assert "ничего не стояло" not in text
    assert "ещё впереди" in text


async def test_tick_with_real_service_without_plans_says_so(session_factory):
    # Настоящий рендер: если weekly_review_for сломается на пустой базе,
    # тест покраснеет (раньше здесь стоял стаб и ничего не проверял).
    from src.bot.wellness_service import WellnessService

    wellness = WellnessService(session_factory, ZONE, clock=lambda: SUNDAY)
    sender = FakeReportSender()
    tick = WeeklyTick(
        session_factory,
        sender.send,
        ZONE,
        time(20, 0),
        wellness.weekly_review_for,
    )

    await tick.run(SUNDAY)

    assert len(sender.sent) == 1
    assert "Планов за эту неделю нет" in sender.sent[0]


# --- Сквозной путь дистанции из БД ----------------------------------------


async def test_distance_reaches_reminder_text(session_factory):
    from src.bot.service import WorkoutService
    from src.core.schedule import Weekday
    from src.scheduler.tick import SchedulerTick as RealTick
    from tests.fakes import FakeSender as RealSender

    monday = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    service = WorkoutService(session_factory, ZONE, clock=lambda: monday)
    service.create_weekly("бег", (Weekday.TUESDAY,), time(8, 0), distance_km=7)
    sender = RealSender()
    tick = RealTick(session_factory, sender, ZONE)

    await tick.run(datetime(2026, 10, 27, 8, 0, tzinfo=UTC))

    # FakeSender хранит ReminderMessage — текст проверяем через форматтер.
    from src.core.messages import format_reminder

    assert "7 км (≈70 мин)" in format_reminder(sender.sent[0], ZONE)


def test_distance_reaches_plan_text(session_factory):
    from src.bot.service import WorkoutService
    from src.bot.wellness_service import WellnessService
    from src.core.schedule import Weekday

    monday = datetime(2026, 10, 26, 21, 0, tzinfo=UTC)
    WorkoutService(session_factory, ZONE, clock=lambda: monday).create_weekly(
        "бег", (Weekday.TUESDAY,), time(8, 0), distance_km=7
    )
    with session_factory() as session:
        repository.save_wellness(session, monday.date(), 3, None, monday)
    service = WellnessService(session_factory, ZONE, clock=lambda: monday)

    text = service.show_plan(monday)

    assert "7 км (≈45 мин, обычно 70 мин)" in text


# --- Claim: право на неделю ------------------------------------------------


def test_claim_is_once_and_release_returns_it(session_factory):
    now = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, now) is True
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, now) is False
    with session_factory() as session:
        assert repository.release_weekly_report(session, WEEK_START) is True
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, now) is True


def test_sent_report_cannot_be_claimed_or_released(session_factory):
    now = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    with session_factory() as session:
        repository.claim_weekly_report(session, WEEK_START, now)
        repository.mark_weekly_report_sent(session, WEEK_START, now)
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, now) is False
        assert repository.release_weekly_report(session, WEEK_START) is False


def test_stale_claim_is_taken_over_after_crash(session_factory):
    # Упавший процесс оставил claim без доставки: через 11 минут право
    # перехватывается, иначе неделя терялась бы молча.
    start = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, start) is True
    later = start + timedelta(minutes=11)
    with session_factory() as session:
        assert repository.claim_weekly_report(session, WEEK_START, later) is True


# --- Сборка job в приложении -----------------------------------------------


def test_build_all_jobs_registers_all_three(tmp_path, session_factory):
    from src.app import build_all_jobs
    from src.scheduler.runner import JOB_ID, WELLNESS_JOB_ID
    from tests.fakes import FakeBot
    from tests.test_app import make_settings

    runtime = type(
        "R", (), {"session_factory": session_factory, "service": None, "wellness": None}
    )()

    scheduler = build_all_jobs(runtime, FakeBot(), make_settings(tmp_path))

    assert {job.id for job in scheduler.get_jobs()} == {
        JOB_ID, WELLNESS_JOB_ID, WEEKLY_JOB_ID
    }
