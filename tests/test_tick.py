"""Тесты тика: один проход находит момент, занимает его и отправляет (4.5).

Время фиксировано, транспорт поддельный — реального ожидания и сети нет.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.core.reminders import ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Reminder
from src.scheduler.runner import JOB_ID, build_scheduler
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeSender

# Понедельник 26.10.2026, 19:30 UTC. Зона UTC, чтобы тест читался без пересчёта.
MOMENT = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)
ZONE = ZoneInfo("UTC")


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def tick(session_factory, sender) -> SchedulerTick:
    return SchedulerTick(session_factory, sender, ZONE)


def make_workout(session_factory, at: time = time(19, 30), weekday=Weekday.MONDAY) -> int:
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(weekday,),
        time_of_day=at,
        duration_min=45,
    )
    with session_factory() as session:
        return repository.create_workout(session, draft).id


def reminder_rows(session_factory) -> list[Reminder]:
    with session_factory() as session:
        return list(session.scalars(select(Reminder).order_by(Reminder.id)))


async def test_first_tick_sends_reminder(tick, sender, session_factory):
    workout_id = make_workout(session_factory)

    result = await tick.run(MOMENT)

    assert len(result.claimed) == 1
    assert len(sender.sent) == 1
    assert sender.sent[0].workout_id == workout_id

    rows = reminder_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].status == ReminderStatus.SENT.value
    assert rows[0].sent_at is not None


async def test_new_instance_restores_without_commands(session_factory, sender):
    # R-10: свежий экземпляр приложения (новый тик на той же базе) напоминает
    # о тренировке без единой команды от пользователя.
    make_workout(session_factory)
    fresh_tick = SchedulerTick(session_factory, sender, ZONE)

    result = await fresh_tick.run(MOMENT)

    assert result.claimed
    assert sender.sent


async def test_second_tick_does_not_send_twice(tick, sender, session_factory):
    make_workout(session_factory)

    first = await tick.run(MOMENT)
    second = await tick.run(MOMENT)

    assert first.claimed
    assert second.claimed == []
    assert len(sender.sent) == 1
    assert len(reminder_rows(session_factory)) == 1


async def test_tick_ignores_other_weekday(tick, sender, session_factory):
    make_workout(session_factory, weekday=Weekday.TUESDAY)

    result = await tick.run(MOMENT)

    assert result.claimed == []
    assert sender.sent == []


async def test_tick_ignores_disabled_workout(tick, sender, session_factory):
    # Второй сценарий R-03: после `/off` следующий тик не создаёт напоминание.
    workout_id = make_workout(session_factory)
    with session_factory() as session:
        repository.deactivate(session, workout_id)

    result = await tick.run(MOMENT)

    assert result.claimed == []
    assert sender.sent == []
    assert reminder_rows(session_factory) == []


def test_deactivate_cancels_pending_reminder(session_factory):
    # Задача 4.6: уже занятый, но не отправленный момент не должен «дожить»
    # до отправки после отключения тренировки.
    workout_id = make_workout(session_factory)
    future = MOMENT + timedelta(days=7)
    with session_factory() as session:
        reminder_id = repository.claim_reminder(session, workout_id, future)
        assert reminder_id is not None
        assert repository.deactivate(session, workout_id)

    assert reminder_rows(session_factory) == []


async def test_cleanup_in_tick_marks_yesterday_sent_as_missed(session_factory, sender):
    # R-08: отправлено вчера — ответить уже нельзя, тик закроет как missed.
    workout_id = make_workout(session_factory)
    yesterday = MOMENT - timedelta(days=1)
    with session_factory() as session:
        reminder_id = repository.claim_reminder(session, workout_id, yesterday)
        repository.mark_reminder_sent(session, reminder_id, 1, yesterday)

    tick = SchedulerTick(session_factory, sender, ZONE)
    result = await tick.run(MOMENT)

    assert result.cleaned == 1
    rows = reminder_rows(session_factory)
    assert rows[0].status == ReminderStatus.MISSED.value
    # Сегодняшний момент при этом создан и отправлен как обычно.
    assert len(rows) == 2
    assert rows[1].status == ReminderStatus.SENT.value


def test_scheduler_registers_interval_tick(tick):
    scheduler = build_scheduler(tick, interval_seconds=15)

    job = scheduler.get_job(JOB_ID)

    assert job is not None
    assert job.trigger.interval.total_seconds() == 15

