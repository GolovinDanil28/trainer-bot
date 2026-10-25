"""Хендлер кнопок: доступ, разбор и два ответа Telegram (5.1, R-05, R-11)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.bot.access import OWNER_KEY
from src.bot.callbacks import on_checkin
from src.core.reminders import ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Checkin, Reminder
from tests.fakes import make_callback_update, make_context

OWNER_ID = 42
MOMENT = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)


@pytest.fixture
def reminder_id(session_factory) -> int:
    draft = WorkoutDraft(
        workout_type=WorkoutType.CARDIO,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=30,
    )
    with session_factory() as session:
        workout = repository.create_workout(session, draft)
        reminder_id = repository.claim_reminder(session, workout.id, MOMENT)
        repository.mark_reminder_sent(session, reminder_id, 1, MOMENT)
    return reminder_id


def context(service):
    return make_context({"workout_service": service, OWNER_KEY: OWNER_ID})


def checkin_count(session_factory) -> int:
    with session_factory() as session:
        return len(list(session.scalars(select(Checkin))))


async def test_owner_tap_records_checkin(service, session_factory, reminder_id):
    update = make_callback_update(f"ci:{reminder_id}:done", user_id=OWNER_ID)

    await on_checkin(update, context(service))

    query = update.callback_query
    assert query.answers == ["Записал: сделал."]
    # Клавиатура снимается: отметка уже есть.
    assert query.markup_edits == [None]
    assert checkin_count(session_factory) == 1


async def test_stranger_tap_changes_nothing(service, session_factory, reminder_id):
    update = make_callback_update(f"ci:{reminder_id}:done", user_id=999)

    await on_checkin(update, context(service))

    query = update.callback_query
    assert query.answers == [""]  # ни текста, ни объяснений
    assert query.markup_edits == []  # чужому даже клавиатуру не трогаем
    assert checkin_count(session_factory) == 0
    with session_factory() as session:
        assert session.get(Reminder, reminder_id).status == ReminderStatus.SENT.value


async def test_broken_callback_is_ignored(service, session_factory):
    update = make_callback_update("xx:1:maybe", user_id=OWNER_ID)

    await on_checkin(update, context(service))

    assert update.callback_query.answers == [""]
    assert checkin_count(session_factory) == 0
