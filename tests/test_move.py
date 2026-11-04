"""Перенос времени тренировки: `/move` и фраза (R-46, R-14)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY
from src.bot.assistant_service import AssistantService
from src.bot.handlers import cmd_move
from src.bot.service import WorkoutService
from src.core.assistant import (
    AssistantAction,
    IntentError,
    parse_intent,
)
from src.core.schedule import (
    CommandError,
    Weekday,
    make_weekly_draft,
    parse_move_command,
)
from src.db import repository
from src.db.context import acting_as
from src.db.models import Reminder
from tests.fakes import FakeLLM, make_context, make_update

ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


# --- Разбор команды -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "workout_id", "moment"),
    [
        ("/move 2 20:00", 2, time(20, 0)),
        ("/move 1 7", 1, time(7, 0)),
        ("/move@TrainerBot 3 18:30", 3, time(18, 30)),
    ],
)
def test_parse_move(text, workout_id, moment):
    draft = parse_move_command(text)

    assert (draft.workout_id, draft.time_of_day) == (workout_id, moment)


@pytest.mark.parametrize(
    "text", ["/move", "/move 2", "/move 2 25:00", "/move x 20:00", "/move 0 20:00", "/off 2 20:00"]
)
def test_bad_move_is_rejected(text):
    with pytest.raises(CommandError):
        parse_move_command(text)


# --- Сервис ----------------------------------------------------------------


def _create(session_factory, user_id=1, at=time(19, 0)) -> int:
    with session_factory() as session, acting_as(user_id):
        return repository.create_workout(
            session, make_weekly_draft("бег", (Weekday.MONDAY, Weekday.WEDNESDAY), at)
        ).id


def test_move_changes_all_days_and_keeps_id(session_factory):
    workout_id = _create(session_factory)
    service = WorkoutService(session_factory, ZONE, clock=lambda: NOW)

    text = service.move(workout_id, time(20, 0))

    assert f"#{workout_id}" in text and "20:00" in text
    with session_factory() as session, acting_as(1):
        rows = session.scalars(
            select(repository.Schedule).where(repository.Schedule.workout_id == workout_id)
        ).all()
        view = repository.list_active(session)
    assert {row.time_of_day for row in rows} == {"20:00"}
    assert view[0].time_of_day == time(20, 0)
    assert view[0].duration_min == 30  # длительность не тронута


def test_move_one_off_keeps_date(session_factory):
    service = WorkoutService(session_factory, ZONE, clock=lambda: NOW)
    service.create_once("05.11", "бег", time(19, 0), today=date(2026, 11, 4))
    workout_id = 1

    text = service.move(workout_id, time(18, 30))

    with session_factory() as session, acting_as(1):
        view = repository.list_active(session)[0]
    assert view.time_once == time(18, 30)
    assert view.date_once == date(2026, 11, 5)
    assert "18:30" in text


def test_move_cancels_pending_reminders(session_factory):
    workout_id = _create(session_factory)
    moment = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)
    with session_factory() as session:
        assert repository.claim_reminder(session, workout_id, moment) is not None

    WorkoutService(session_factory, ZONE, clock=lambda: NOW).move(
        workout_id, time(20, 0)
    )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Reminder)) == 0


def test_move_unknown_workout(session_factory):
    service = WorkoutService(session_factory, ZONE, clock=lambda: NOW)

    text = service.move(99, time(20, 0))

    assert "#99" in text and "нет" in text.lower()


def test_move_foreign_workout_is_not_found(session_factory):
    workout_id = _create(session_factory, user_id=1)
    with session_factory() as session:
        repository.create_user(session, 555, "UTC", "21:00", "20:00")

    with acting_as(2):
        text = WorkoutService(session_factory, ZONE, clock=lambda: NOW).move(
            workout_id, time(20, 0)
        )

    assert "#1" in text and "нет" in text.lower()
    with session_factory() as session, acting_as(1):
        assert repository.list_active(session)[0].time_of_day == time(19, 0)


def test_move_command_handler(session_factory):
    workout_id = _create(session_factory)
    service = WorkoutService(session_factory, ZONE, clock=lambda: NOW)
    update = make_update(f"/move {workout_id} 21")

    import asyncio

    context = make_context({"workout_service": service, OWNER_KEY: 42})
    asyncio.run(cmd_move(update, context))

    assert "21:00" in update.message.sent[0]


# --- Фраза -----------------------------------------------------------------


def test_intent_move():
    intent = parse_intent(
        '{"action":"move","workout_id":2,"time":"20:00"}',
        "перенеси вторую тренировку на восемь вечера",
    )

    assert intent.action is AssistantAction.MOVE
    assert (intent.workout_id, intent.time) == (2, "20:00")


def test_move_without_move_word_is_rejected():
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"move","workout_id":2,"time":"20:00"}',
            "вторая тренировка в 20:00",
        )


async def test_phrase_moves_workout(session_factory):
    _create(session_factory, at=time(19, 0))
    _create(session_factory, at=time(8, 0))
    service = WorkoutService(session_factory, ZONE, clock=lambda: NOW)
    phrase = "перенеси вторую тренировку на восемь вечера"
    answer = '{"action":"move","workout_id":2,"time":"20:00"}'
    assistant = AssistantService(FakeLLM(answers={phrase: answer}), service, "test", 1.0)

    text = await assistant.handle(phrase)

    assert "20:00" in text
    with session_factory() as session, acting_as(1):
        views = repository.list_active(session)
    assert views[1].time_of_day == time(20, 0)
    assert views[0].time_of_day == time(19, 0)
