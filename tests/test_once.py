"""Разовая тренировка на дату (R-35): резолв даты, создание, тик, списки, план."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY
from src.bot.assistant_service import AssistantService
from src.bot.service import WorkoutService
from src.core.assistant import IntentError, parse_intent
from src.core.plan import build_plan
from src.core.schedule import (
    Weekday,
    WorkoutView,
    make_once_draft,
    make_weekly_draft,
    resolve_date,
)
from src.core.wellness import WellnessScore
from src.db import repository
from src.db.context import DEFAULT_USER_ID
from src.db.models import Reminder, Schedule, Workout
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeLLM, FakeSender

# Понедельник 26.10.2026: дата фиксирована, чтобы «завтра» не зависело от дня
# прогона (правило §9).
TODAY = date(2026, 10, 26)
MOMENT = datetime(2026, 10, 27, 19, 30, tzinfo=UTC)
ZONE = ZoneInfo("UTC")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("сегодня", date(2026, 10, 26)),
        ("завтра", date(2026, 10, 27)),
        ("послезавтра", date(2026, 10, 28)),
        ("28.10", date(2026, 10, 28)),
        ("26.10", date(2026, 10, 26)),
        # Без года и уже прошло — следующий год, а не прошлое.
        ("25.10", date(2027, 10, 25)),
        # Явный год в прошлом — отказ, а не догадка.
        ("25.10.2025", None),
        ("28.10.2027", date(2027, 10, 28)),
        ("31.02", None),
        ("28/10", None),
        ("когда-нибудь", None),
    ],
)
def test_resolve_date(raw, expected):
    assert resolve_date(raw, TODAY) == expected


def test_resolve_date_across_new_year():
    assert resolve_date("01.01", date(2026, 12, 31)) == date(2027, 1, 1)


# --- Разбор ответа модели -------------------------------------------------


def test_intent_with_date_has_no_days():
    intent = parse_intent(
        '{"action":"create","type":"бег","date_raw":"завтра","time":"19:00",'
        '"distance_km":7}',
        "бег завтра в 19:00 на 7 км",
    )

    assert intent.date_raw == "завтра"
    assert intent.days is None


def test_numeric_date_passes_grounding():
    # Точка в «28.10» не даёт слову попасть в токены — проверка опоры обязана
    # смотреть на нормализованный текст, а не на множество слов.
    intent = parse_intent(
        '{"action":"create","type":"бег","date_raw":"28.10","time":"16:00"}',
        "бег 28.10 в 16:00",
    )

    assert intent.date_raw == "28.10"


def test_invented_numeric_date_is_rejected():
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","date_raw":"28.10","time":"16:00"}',
            "бег в 16:00",
        )


def test_intent_rejects_days_and_date_together():
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","days":["Пн"],"date_raw":"завтра",'
            '"time":"19:00"}',
            "бег в понедельник завтра в 19:00",
        )


def test_intent_rejects_bad_date_token():
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","date_raw":"в следующую пятницу",'
            '"time":"19:00"}',
            "бег в следующую пятницу в 19:00",
        )


def test_date_must_be_mentioned_in_text():
    # Модель не может назначить дату, которой нет во фразе (R-15).
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","date_raw":"завтра","time":"19:00"}',
            "бег фиг знает когда в 19:00",
        )


# --- Создание и показ -----------------------------------------------------


def test_once_draft_has_no_schedule_rows(session_factory):
    draft = make_once_draft("бег", date(2026, 10, 28), time(16, 0), distance_km=7)

    with session_factory() as session:
        view = repository.create_workout(session, draft)
        schedules = session.scalar(select(func.count()).select_from(Schedule))

    assert view.date_once == date(2026, 10, 28)
    assert view.time_once == time(16, 0)
    assert view.duration_min == 70
    assert schedules == 0


def test_past_date_with_year_is_refused_by_service(service, session_factory):
    text = service.create_once(
        "25.10.2025", "бег", time(16, 0), today=TODAY
    )

    assert "Не понял дату" in text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


def test_list_shows_once_with_date_and_hides_past(session_factory):
    # Часы сервиса фиксированы: тест не флапает на границе суток (§9).
    zone = ZoneInfo("Asia/Yekaterinburg")
    fixed_now = datetime(2026, 10, 29, 12, 0, tzinfo=zone)
    service = WorkoutService(session_factory, zone, clock=lambda: fixed_now)
    tomorrow = date(2026, 10, 30)

    service.create_once("завтра", "бег", time(16, 0))
    # Прошедшая разовая кладётся напрямую: через сервис её не создать, а фильтр
    # списка обязан её скрыть (иначе сценарий R-35 не проверяется вовсе).
    with session_factory() as session:
        repository.create_workout(
            session, make_once_draft("бег", date(2026, 10, 1), time(16, 0))
        )

    text = service.show_schedule()

    assert tomorrow.strftime("%d.%m") in text
    assert "01.10" not in text


# --- Тик ------------------------------------------------------------------


def _once_workout(session_factory, day: date) -> int:
    with session_factory() as session:
        return repository.create_workout(
            session, make_once_draft("бег", day, time(19, 30), duration_min=70)
        ).id


async def test_tick_reminds_once_and_not_twice(session_factory):
    workout_id = _once_workout(session_factory, date(2026, 10, 27))
    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE)

    first = await tick.run(MOMENT)
    second = await tick.run(MOMENT)

    assert len(first.claimed) == 1
    assert len(sender.sent) == 1
    assert sender.sent[0].workout_id == workout_id
    assert second.claimed == []
    with session_factory() as session:
        rows = session.scalars(select(Reminder)).all()
    assert len(rows) == 1


async def test_tick_ignores_once_on_another_day(session_factory):
    _once_workout(session_factory, date(2026, 10, 28))
    sender = FakeSender()

    await SchedulerTick(session_factory, sender, ZONE).run(MOMENT)

    assert sender.sent == []


# --- План на завтра -------------------------------------------------------


def _once_view(day: date) -> WorkoutView:
    return WorkoutView(
        id=1,
        workout_type="бег",
        weekdays=(),
        time_of_day=time(19, 0),
        duration_min=70,
        distance_km=7,
        date_once=day,
        time_once=time(19, 0),
    )


def test_plan_includes_once_on_tomorrow():
    tomorrow = date(2026, 10, 27)
    plan = build_plan(WellnessScore(4), [_once_view(tomorrow)])

    assert plan.has_workouts
    assert plan.items[0].workout_type == "бег"


def test_weekly_and_once_are_both_in_plan(session_factory):
    tomorrow = date(2026, 10, 27)
    with session_factory() as session:
        repository.create_workout(
            session,
            make_weekly_draft(
                "силовая", (Weekday(int(tomorrow.weekday())),), time(8, 0)
            ),
        )
        repository.create_workout(
            session, make_once_draft("бег", tomorrow, time(19, 0), distance_km=7)
        )
        workouts = repository.workouts_for_day(session, tomorrow)

    assert sorted(item.workout_type for item in workouts) == ["бег", "силовая"]


# --- Свободный текст ведёт к разовой --------------------------------------


async def test_phrase_with_date_creates_once(service, session_factory):
    phrase = "бег завтра в 19:00 на 7 км"
    answer = (
        '{"action":"create","type":"бег","date_raw":"завтра","time":"19:00",'
        '"distance_km":7}'
    )
    assistant = AssistantService(FakeLLM(answers={phrase: answer}), service, "test", 1.0)

    text = await assistant.handle(phrase)

    assert "Готово" in text
    tomorrow = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date() + timedelta(days=1)
    with session_factory() as session:
        workout = session.scalar(select(Workout))
        schedules = session.scalar(select(func.count()).select_from(Schedule))
    assert workout is not None and workout.date_once == tomorrow
    assert schedules == 0


async def test_choice_button_keeps_date_from_phrase(service, session_factory):
    """«или» + дата: кнопка выбирает тип, дата остаётся из фразы (R-34/R-35)."""
    from src.bot.callbacks import on_type_choice
    from src.bot.pending import PendingChoices
    from src.core.choice import build_type_choice_callback
    from tests.fakes import FakeBot, make_callback_update, make_context

    phrase = "бег или интервальный бег завтра в 19:00"
    answer = (
        '{"action":"create","type":null,'
        '"type_options":["бег","интервальный бег"],'
        '"date_raw":"завтра","time":"19:00"}'
    )
    assistant = AssistantService(FakeLLM(answers={phrase: answer}), service, "test", 1.0)
    pending = PendingChoices()
    context = make_context(
        {
            "workout_service": service,
            "assistant_service": assistant,
            "pending_choices": pending,
            OWNER_KEY: 42,
        },
        bot=FakeBot(),
    )

    turn = await assistant.handle_turn(phrase)
    choice = pending.put(DEFAULT_USER_ID, turn.intent)
    click = make_callback_update(build_type_choice_callback(choice.id, 0))

    await on_type_choice(click, context)

    tomorrow = date.today() + timedelta(days=1)
    with session_factory() as session:
        workout = session.scalar(select(Workout))
        schedules = session.scalar(select(func.count()).select_from(Schedule))
    assert workout is not None
    assert workout.date_once == tomorrow
    assert workout.time_once == "19:00"
    assert schedules == 0
