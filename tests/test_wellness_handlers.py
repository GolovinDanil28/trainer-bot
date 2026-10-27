"""Сервис самочувствия, команды и кнопки (R-20…R-26).

Telegram не поднимается: команды проверяются строкой на входе и текстом на
выходе, кнопки — подделкой callback-запроса из `tests/fakes.py`.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY, REFUSAL_TEXT
from src.bot.callbacks import on_wellness
from src.bot.handlers import cmd_plan, cmd_wellness
from src.bot.wellness_service import WellnessService
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.core.wellness import WellnessEntry, wellness_callback
from src.db import repository
from src.db.models import Reminder, Schedule, Wellness, Workout
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeSender, make_callback_update, make_context, make_update

OWNER_ID = 42
ZONE = ZoneInfo("Asia/Yekaterinburg")
# 18:00 UTC = 23:00 в Екатеринбурге: «сегодня» и «завтра» — точно местные дни.
NOW_UTC = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)
TODAY = date(2026, 10, 27)
TOMORROW = date(2026, 10, 28)  # среда


@pytest.fixture
def wellness(session_factory) -> WellnessService:
    # Часы фиксированы: иначе «кнопка от вчерашнего вопроса» зависела бы от дня
    # запуска тестов (AGENTS.md §9).
    return WellnessService(session_factory, ZONE, clock=lambda: NOW_UTC)


@pytest.fixture
def tomorrow_workout(session_factory) -> int:
    """Силовая на 45 минут по средам — то есть завтра."""
    return make_workout(session_factory, Weekday.WEDNESDAY)


@pytest.fixture
def thursday_workout(session_factory) -> int:
    """Силовая по четвергам: нужна, когда «завтра» наступает через сутки."""
    return make_workout(session_factory, Weekday.THURSDAY)


def make_workout(
    session_factory,
    weekday: Weekday,
    hour: int = 19,
    duration_min: int = 45,
) -> int:
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(weekday,),
        time_of_day=time(hour, 30),
        duration_min=duration_min,
    )
    with session_factory() as session:
        return repository.create_workout(session, draft).id


def entry_for(session_factory, day: date = TODAY) -> WellnessEntry | None:
    with session_factory() as session:
        return repository.wellness_for_day(session, day)


def snapshot(session_factory) -> tuple[int, int, int, int, int | None]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(Workout)),
            session.scalar(select(func.count()).select_from(Schedule)),
            session.scalar(select(func.count()).select_from(Reminder)),
            session.scalar(select(func.count()).select_from(Wellness)),
            session.scalar(select(Wellness.score).where(Wellness.day == TODAY.isoformat())),
        )


# --- команда --------------------------------------------------------------


def test_command_saves_score_and_shows_plan(wellness, session_factory, tomorrow_workout):
    text = wellness.record_command("/wellness 3", NOW_UTC)

    assert text.startswith("Записал самочувствие: 3/5.")
    assert "силовая, 19:30, 30 мин" in text
    assert "обычно 45" in text
    assert entry_for(session_factory) == WellnessEntry(TODAY, 3, None)


def test_command_saves_note_and_rest_plan(wellness, session_factory, tomorrow_workout):
    text = wellness.record_command("/wellness 2 не выспался", NOW_UTC)

    assert entry_for(session_factory) == WellnessEntry(TODAY, 2, "не выспался")
    assert "отдых" in text.lower()


def test_command_invalid_changes_nothing(wellness, session_factory):
    text = wellness.record_command("/wellness 7", NOW_UTC)

    assert "/wellness 3" in text
    assert entry_for(session_factory) is None


def test_command_without_argument_returns_hint(wellness, session_factory):
    wellness.record_command("/wellness 4", NOW_UTC)

    text = wellness.record_command("/wellness", NOW_UTC)

    assert "Нужна оценка от 1 до 5" in text
    entry = entry_for(session_factory)
    assert entry is not None
    assert entry.score == 4


def test_command_without_note_keeps_previous_note(wellness, session_factory):
    wellness.record_command("/wellness 3 устал", NOW_UTC)

    wellness.record_command("/wellness 5", NOW_UTC)

    entry = entry_for(session_factory)
    assert entry is not None
    assert entry.score == 5
    assert entry.note == "устал"


def test_command_twice_keeps_last_score(wellness, session_factory):
    wellness.record_command("/wellness 4", NOW_UTC)
    wellness.record_command("/wellness 2", NOW_UTC)

    entry = entry_for(session_factory)

    assert entry is not None
    assert entry.score == 2


def test_command_without_workouts_tomorrow(wellness, session_factory):
    text = wellness.record_command("/wellness 4", NOW_UTC)

    assert "тренировок нет" in text


def test_local_day_wins_over_utc_day(wellness, session_factory, thursday_workout):
    # 20:30 UTC 27-го = 01:30 местного 28-го: «сегодня» — 28-е, «завтра» — 29-е.
    # Если считать день по UTC, оценка ляжет на 27-е, а план — на 28-е (среда),
    # где тренировки нет. Тест ловит именно эту подмену.
    late_night = datetime(2026, 10, 27, 20, 30, tzinfo=UTC)

    text = wellness.record_command("/wellness 3", late_night)

    assert entry_for(session_factory, date(2026, 10, 28)) == WellnessEntry(
        date(2026, 10, 28), 3, None
    )
    assert entry_for(session_factory, date(2026, 10, 27)) is None
    assert "силовая, 19:30, 30 мин" in text


# --- план -----------------------------------------------------------------


def test_plan_without_score_is_a_hint(wellness):
    text = wellness.show_plan(NOW_UTC)

    assert "не отмечено" in text
    assert "/wellness 3" in text


def test_plan_after_command(wellness, tomorrow_workout):
    wellness.record_command("/wellness 5", NOW_UTC)

    text = wellness.show_plan(NOW_UTC)

    assert "силовая, 19:30, 45 мин" in text
    assert "обычно" not in text


def test_plan_does_not_change_state(wellness, session_factory, tomorrow_workout):
    wellness.record_command("/wellness 4", NOW_UTC)
    before = snapshot(session_factory)

    wellness.show_plan(NOW_UTC)

    assert snapshot(session_factory) == before


# --- кнопка ---------------------------------------------------------------


def test_button_saves_score_for_today(wellness, session_factory, tomorrow_workout):
    result = wellness.record_from_button(TODAY, 3, NOW_UTC)

    assert result.accepted is True
    assert "силовая, 19:30, 30 мин" in result.text
    assert entry_for(session_factory) == WellnessEntry(TODAY, 3, None)


def test_button_repeat_replaces_score(wellness, session_factory, tomorrow_workout):
    wellness.record_from_button(TODAY, 3, NOW_UTC)

    result = wellness.record_from_button(TODAY, 5, NOW_UTC)

    assert result.accepted is True
    entry = entry_for(session_factory)
    assert entry is not None
    assert entry.score == 5
    # План пересчитан по правилу для 5: длительность как в расписании.
    assert "силовая, 19:30, 45 мин" in result.text
    assert "облегчённый" not in result.text


def test_button_from_yesterday_does_not_touch_today(wellness, session_factory):
    wellness.record_from_button(TODAY, 4, NOW_UTC)

    result = wellness.record_from_button(TODAY - timedelta(days=1), 1, NOW_UTC)

    assert result.accepted is False
    assert "неактуален" in result.text
    entry = entry_for(session_factory)
    assert entry is not None
    assert entry.score == 4
    # «Не сохраняется ни за вчера» — тоже обещание R-20.
    assert entry_for(session_factory, TODAY - timedelta(days=1)) is None


def test_button_rejects_invalid_score_before_write(wellness, session_factory):
    # Оценка проверяется до записи: иначе 9 легло бы в базу и упало уже на плане.
    with pytest.raises(ValueError):
        wellness.record_from_button(TODAY, 9, NOW_UTC)

    assert entry_for(session_factory) is None


# --- хендлеры -------------------------------------------------------------


def wellness_context(service: WellnessService):
    return make_context({"wellness_service": service, OWNER_KEY: OWNER_ID})


async def test_cmd_wellness_replies_with_plan(wellness, tomorrow_workout):
    update = make_update("/wellness 3")

    await cmd_wellness(update, wellness_context(wellness))

    assert "Записал самочувствие: 3/5." in update.message.sent[0]


async def test_cmd_plan_replies(wellness):
    update = make_update("/plan")

    await cmd_plan(update, wellness_context(wellness))

    assert "не отмечено" in update.message.sent[0]


async def test_cmd_plan_after_score_shows_plan(wellness, tomorrow_workout):
    wellness.record_command("/wellness 3", NOW_UTC)
    update = make_update("/plan")

    await cmd_plan(update, wellness_context(wellness))

    assert "силовая, 19:30, 30 мин" in update.message.sent[0]


async def test_foreign_user_cannot_rate(wellness, session_factory):
    update = make_update("/wellness 3", user_id=99)

    await cmd_wellness(update, wellness_context(wellness))

    assert update.message.sent == [REFUSAL_TEXT]
    assert entry_for(session_factory) is None


async def test_foreign_user_cannot_see_plan(wellness):
    update = make_update("/plan", user_id=99)

    await cmd_plan(update, wellness_context(wellness))

    assert update.message.sent == [REFUSAL_TEXT]


# --- callback -------------------------------------------------------------


async def test_callback_edits_question_into_plan(wellness, tomorrow_workout):
    update = make_callback_update(wellness_callback(TODAY, 3))

    await on_wellness(update, wellness_context(wellness))

    query = update.callback_query
    assert query.answers == [""]
    assert len(query.text_edits) == 1
    assert "силовая, 19:30, 30 мин" in query.text_edits[0]
    # Клавиатура снимается явно: иначе кнопки остаются живыми под ответом.
    assert query.text_edit_markups == [None]


async def test_callback_from_yesterday_is_stale(wellness, session_factory):
    update = make_callback_update(
        wellness_callback(TODAY - timedelta(days=1), 3)
    )

    await on_wellness(update, wellness_context(wellness))

    query = update.callback_query
    assert "неактуален" in query.answers[0]
    assert query.text_edits == []
    assert query.markup_edits == [None]
    assert entry_for(session_factory) is None
    assert entry_for(session_factory, TODAY - timedelta(days=1)) is None


async def test_callback_from_foreign_user_changes_nothing(wellness, session_factory):
    update = make_callback_update(wellness_callback(TODAY, 3), user_id=99)

    await on_wellness(update, wellness_context(wellness))

    query = update.callback_query
    assert query.answers == [""]
    assert query.text_edits == []
    assert entry_for(session_factory) is None


# --- сквозной сценарий ----------------------------------------------------


async def test_plan_does_not_cancel_tomorrow_reminder(
    wellness, service, session_factory, tomorrow_workout
):
    """R-24: план «отдых» — рекомендация, напоминание уходит по расписанию."""
    wellness.record_command("/wellness 1", NOW_UTC)
    plan_text = wellness.show_plan(NOW_UTC)
    assert "отдых" in plan_text.lower()

    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE)
    # 14:30 UTC = 19:30 местного 28-го — момент завтрашней тренировки.
    result = await tick.run(datetime(2026, 10, 28, 14, 30, tzinfo=UTC))

    assert len(result.claimed) == 1
    assert len(sender.sent) == 1
    # Расписание не изменилось: тренировка всё ещё активна.
    assert "силовая" in service.show_schedule()
