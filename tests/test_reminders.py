"""Тесты идемпотентной выдачи напоминаний: кто занял момент, тот и отправляет."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.core.reminders import ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Reminder

MOMENT = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)  # 19:30 по Екатеринбургу


@pytest.fixture
def workout_id(session_factory) -> int:
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=45,
    )
    with session_factory() as session:
        return repository.create_workout(session, draft).id


def test_first_claim_creates_pending_reminder(session_factory, workout_id):
    with session_factory() as session:
        reminder_id = repository.claim_reminder(session, workout_id, MOMENT)

        reminder = session.get(Reminder, reminder_id)

    assert reminder_id is not None
    assert reminder is not None
    assert reminder.status == ReminderStatus.PENDING.value
    assert reminder.attempts == 0
    assert reminder.sent_at is None


def test_second_claim_of_same_moment_is_refused(session_factory, workout_id):
    with session_factory() as session:
        first = repository.claim_reminder(session, workout_id, MOMENT)
        second = repository.claim_reminder(session, workout_id, MOMENT)

        total = session.scalar(select(func.count()).select_from(Reminder))

    assert first is not None
    # Именно так работает защита от двойной отправки после рестарта:
    # второй проход не получает id и молча пропускает момент (R-06).
    assert second is None
    assert total == 1


def test_different_moments_get_separate_reminders(session_factory, workout_id):
    next_week = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)

    with session_factory() as session:
        first = repository.claim_reminder(session, workout_id, MOMENT)
        second = repository.claim_reminder(session, workout_id, next_week)

        total = session.scalar(select(func.count()).select_from(Reminder))

    assert first != second
    assert total == 2


def test_claim_stores_utc_naive_in_database(session_factory, workout_id):
    # SQLite не хранит таймзону: в базе лежит 14:30, хотя на входе был 14:30Z.
    # Это нормализация на границе БД, а не потеря информации.
    with session_factory() as session:
        reminder_id = repository.claim_reminder(session, workout_id, MOMENT)
        stored = session.scalar(select(Reminder.scheduled_at).where(Reminder.id == reminder_id))

    assert stored == datetime(2026, 10, 26, 14, 30)  # без tzinfo
    assert stored.tzinfo is None


def test_local_time_is_converted_to_utc_before_saving(session_factory, workout_id):
    # 19:30 в Екатеринбурге (UTC+5) — это 14:30 UTC. Если бы мы сохранили
    # локальное время как есть, напоминание ушло бы на 5 часов позже.
    local_moment = datetime(2026, 10, 26, 19, 30, tzinfo=ZoneInfo("Asia/Yekaterinburg"))

    with session_factory() as session:
        reminder_id = repository.claim_reminder(session, workout_id, local_moment)
        stored = session.scalar(select(Reminder.scheduled_at).where(Reminder.id == reminder_id))

    assert stored == datetime(2026, 10, 26, 14, 30)


def test_naive_moment_is_rejected(session_factory, workout_id):
    with session_factory() as session, pytest.raises(ValueError, match="timezone-aware"):
        repository.claim_reminder(session, workout_id, datetime(2026, 10, 26, 14, 30))
