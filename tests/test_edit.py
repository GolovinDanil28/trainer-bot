"""Редактирование тренировки: тип, дни, дата, время (R-48)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY
from src.bot.handlers import cmd_edit
from src.bot.service import WorkoutService
from src.core.schedule import (
    CommandError,
    Weekday,
    make_once_draft,
    make_weekly_draft,
    parse_edit_command,
)
from src.db import repository
from src.db.context import acting_as
from src.db.models import Reminder, Schedule
from tests.fakes import make_context, make_update

ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# --- Разбор ---------------------------------------------------------------


def test_parse_edit_fields():
    assert parse_edit_command("/edit 2 type интервальный бег").workout_type == "интервальный бег"
    assert parse_edit_command("/edit 2 days по пятницам").weekdays == (Weekday.FRIDAY,)
    assert parse_edit_command("/edit 3 date завтра").date_raw == "завтра"
    assert parse_edit_command("/edit 1 time 20").time_of_day == time(20, 0)


@pytest.mark.parametrize(
    "text",
    [
        "/edit",
        "/edit 2",
        "/edit 2 days",
        "/edit 0 days Пн",
        "/edit 2 days Пн,Сббота",
        "/edit 2 colour red",
        "/edit 2 time 25:00",
        "/edit 2 date 1 2",
    ],
)
def test_bad_edit_rejected(text):
    with pytest.raises(CommandError):
        parse_edit_command(text)


# --- Сервис ----------------------------------------------------------------


def _weekly(session_factory) -> int:
    with session_factory() as session, acting_as(1):
        return repository.create_workout(
            session,
            make_weekly_draft(
                "бег", (Weekday.MONDAY, Weekday.WEDNESDAY), time(19, 0), distance_km=7
            ),
        ).id


def _once(session_factory) -> int:
    with session_factory() as session, acting_as(1):
        return repository.create_workout(
            session, make_once_draft("бег", date(2026, 11, 5), time(19, 0))
        ).id


def _service(session_factory) -> WorkoutService:
    return WorkoutService(session_factory, ZONE, clock=lambda: NOW)


def test_type_change_keeps_schedule_and_volume(session_factory):
    workout_id = _weekly(session_factory)

    text = _service(session_factory).edit_from_command(f"/edit {workout_id} type силовая")

    with session_factory() as session, acting_as(1):
        view = repository.list_active(session)[0]
    assert view.workout_type == "силовая"
    assert view.time_of_day == time(19, 0)
    assert view.duration_min == 70
    assert view.weekdays == (Weekday.MONDAY, Weekday.WEDNESDAY)
    assert "силовая" in text


def test_days_change_replaces_schedule_and_cancels_pending(session_factory):
    workout_id = _weekly(session_factory)
    moment = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)
    with session_factory() as session:
        assert repository.claim_reminder(session, workout_id, moment) is not None

    _service(session_factory).edit_from_command(f"/edit {workout_id} days Пн,Пт")

    with session_factory() as session, acting_as(1):
        view = repository.list_active(session)[0]
        schedules = session.scalars(
            select(Schedule).where(Schedule.workout_id == workout_id)
        ).all()
        pending = session.scalar(select(func.count()).select_from(Reminder))
    assert view.weekdays == (Weekday.MONDAY, Weekday.FRIDAY)
    assert view.time_of_day == time(19, 0)
    assert len(schedules) == 2
    assert pending == 0


def test_date_change_for_once(session_factory):
    workout_id = _once(session_factory)

    _service(session_factory).edit_from_command(f"/edit {workout_id} date 07.11")

    with session_factory() as session, acting_as(1):
        view = repository.list_active(session)[0]
    assert view.date_once == date(2026, 11, 7)
    assert view.time_of_day == time(19, 0)


def test_date_in_past_is_refused(session_factory):
    workout_id = _once(session_factory)

    text = _service(session_factory).edit_from_command(
        f"/edit {workout_id} date 07.11.2020"
    )

    assert "Не понял дату" in text
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].date_once == date(2026, 11, 5)


def test_days_on_once_is_refused(session_factory):
    workout_id = _once(session_factory)

    text = _service(session_factory).edit_from_command(f"/edit {workout_id} days Пн")

    assert "разовая" in text.lower()
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].date_once == date(2026, 11, 5)


def test_date_on_weekly_is_refused(session_factory):
    workout_id = _weekly(session_factory)

    text = _service(session_factory).edit_from_command(f"/edit {workout_id} date 07.11")

    assert "недельная" in text.lower()


def test_time_edit_matches_move(session_factory):
    workout_id = _weekly(session_factory)

    text = _service(session_factory).edit_from_command(f"/edit {workout_id} time 20:00")

    assert "20:00" in text
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].time_of_day == time(20, 0)


def test_unknown_and_foreign_edit(session_factory):
    workout_id = _weekly(session_factory)
    with session_factory() as session:
        repository.create_user(session, 555, "UTC", "21:00", "20:00")
    service = _service(session_factory)

    unknown = service.edit_from_command("/edit 99 type бег")
    with acting_as(2):
        foreign = service.edit_from_command(f"/edit {workout_id} type бег")

    assert "нет" in unknown.lower()
    assert "нет" in foreign.lower()
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].workout_type == "бег"


# --- Хендлер ---------------------------------------------------------------


def test_edit_handler(session_factory):
    workout_id = _weekly(session_factory)
    service = _service(session_factory)
    update = make_update(f"/edit {workout_id} type силовая")
    context = make_context({"workout_service": service, OWNER_KEY: 42})

    import asyncio

    asyncio.run(cmd_edit(update, context))

    assert "силовая" in update.message.sent[0]
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].workout_type == "силовая"
