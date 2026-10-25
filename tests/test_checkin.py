"""Отметки: запись в журнал и неизменность первой (5.1, 5.2 — R-11, R-12).

Проверяем через сервис, как и команды: разбор callback_data и тексты ответов
уже покрыты отдельно, здесь важен эффект в базе.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.core.reminders import CheckinAction, CheckinStatus, ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Checkin, Reminder

MOMENT = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)


@pytest.fixture
def reminder_id(session_factory) -> int:
    """Отправленное напоминание — то, под которым висят кнопки."""
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=45,
    )
    with session_factory() as session:
        workout = repository.create_workout(session, draft)
        reminder_id = repository.claim_reminder(session, workout.id, MOMENT)
        repository.mark_reminder_sent(session, reminder_id, 1, MOMENT)
    return reminder_id


def checkins(session_factory) -> list[Checkin]:
    with session_factory() as session:
        return list(session.scalars(select(Checkin).order_by(Checkin.id)))


def reminder_status(session_factory, reminder_id: int) -> str:
    with session_factory() as session:
        return session.get(Reminder, reminder_id).status


def test_done_is_recorded(service, session_factory, reminder_id):
    text = service.record_checkin(reminder_id, CheckinAction.DONE)

    assert text == "Записал: сделал."
    rows = checkins(session_factory)
    assert len(rows) == 1
    assert rows[0].status == CheckinStatus.DONE.value
    assert rows[0].reminder_id == reminder_id
    assert reminder_status(session_factory, reminder_id) == ReminderStatus.ANSWERED.value


def test_skip_is_recorded_as_skipped(service, session_factory, reminder_id):
    # В кнопке `skip`, в журнале `skipped` — это разные словари (design D3/D4).
    text = service.record_checkin(reminder_id, CheckinAction.SKIP)

    assert text == "Записал: пропустил."
    assert checkins(session_factory)[0].status == CheckinStatus.SKIPPED.value


def test_second_tap_keeps_the_first_checkin(service, session_factory, reminder_id):
    service.record_checkin(reminder_id, CheckinAction.DONE)

    text = service.record_checkin(reminder_id, CheckinAction.DONE)

    assert text == "Уже отмечено: сделал. Изменить нельзя."
    rows = checkins(session_factory)
    assert len(rows) == 1
    assert rows[0].status == CheckinStatus.DONE.value


def test_changing_the_mind_is_refused(service, session_factory, reminder_id):
    service.record_checkin(reminder_id, CheckinAction.DONE)

    text = service.record_checkin(reminder_id, CheckinAction.SKIP)

    assert text == "Уже отмечено: сделал. Изменить нельзя."
    assert checkins(session_factory)[0].status == CheckinStatus.DONE.value


def test_unknown_reminder_changes_nothing(service, session_factory):
    text = service.record_checkin(999, CheckinAction.DONE)

    assert text == "Это напоминание больше не активно."
    assert checkins(session_factory) == []
