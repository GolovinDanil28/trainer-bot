"""Уборщик старых напоминаний: границы приходят готовыми моментами (4.4, R-08).

Проверяем именно порог: что раньше границы — `missed`, что позже — не тронуто.
Вычисление самой границы («до конца местных суток») живёт в `core/timeplan`.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from src.core.reminders import ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Reminder

NOW = datetime(2026, 10, 26, 19, 0, tzinfo=UTC)


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


def claim(session_factory, workout_id: int, moment: datetime = NOW) -> int:
    with session_factory() as session:
        return repository.claim_reminder(session, workout_id, moment)


def set_reminder(
    session_factory,
    reminder_id: int,
    *,
    status: ReminderStatus,
    sent_at: datetime | None = None,
    created_at: datetime | None = None,
) -> None:
    values: dict = {"status": status.value}
    if sent_at is not None:
        values["sent_at"] = sent_at
    if created_at is not None:
        values["created_at"] = created_at
    with session_factory() as session:
        session.execute(update(Reminder).where(Reminder.id == reminder_id).values(values))
        session.commit()


def cleanup(session_factory) -> int:
    with session_factory() as session:
        return repository.cleanup_old_reminders(
            session,
            sent_before=NOW - timedelta(hours=1),
            pending_before=NOW - timedelta(minutes=5),
        )


def status_of(session_factory, reminder_id: int) -> str:
    with session_factory() as session:
        return session.get(Reminder, reminder_id).status


def test_sent_before_cutoff_becomes_missed(session_factory, workout_id):
    reminder_id = claim(session_factory, workout_id)
    set_reminder(
        session_factory, reminder_id, status=ReminderStatus.SENT, sent_at=NOW - timedelta(hours=2)
    )

    assert cleanup(session_factory) == 1
    assert status_of(session_factory, reminder_id) == ReminderStatus.MISSED.value


def test_sent_inside_window_is_kept(session_factory, workout_id):
    # Пользователь ещё может ответить: окно не истекло.
    reminder_id = claim(session_factory, workout_id)
    set_reminder(
        session_factory,
        reminder_id,
        status=ReminderStatus.SENT,
        sent_at=NOW - timedelta(minutes=10),
    )

    assert cleanup(session_factory) == 0
    assert status_of(session_factory, reminder_id) == ReminderStatus.SENT.value


def test_stuck_pending_becomes_missed(session_factory, workout_id):
    # Падение между вставкой и отправкой: момент занят, но не отправлен.
    reminder_id = claim(session_factory, workout_id)
    set_reminder(
        session_factory,
        reminder_id,
        status=ReminderStatus.PENDING,
        created_at=NOW - timedelta(minutes=10),
    )

    assert cleanup(session_factory) == 1
    assert status_of(session_factory, reminder_id) == ReminderStatus.MISSED.value


def test_fresh_pending_is_kept(session_factory, workout_id):
    reminder_id = claim(session_factory, workout_id)
    set_reminder(
        session_factory,
        reminder_id,
        status=ReminderStatus.PENDING,
        created_at=NOW - timedelta(minutes=1),
    )

    assert cleanup(session_factory) == 0
    assert status_of(session_factory, reminder_id) == ReminderStatus.PENDING.value


def test_answered_is_never_touched(session_factory, workout_id):
    reminder_id = claim(session_factory, workout_id)
    set_reminder(
        session_factory,
        reminder_id,
        status=ReminderStatus.ANSWERED,
        sent_at=NOW - timedelta(days=1),
        created_at=NOW - timedelta(days=1),
    )

    assert cleanup(session_factory) == 0
    assert status_of(session_factory, reminder_id) == ReminderStatus.ANSWERED.value
