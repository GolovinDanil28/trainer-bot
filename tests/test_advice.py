"""Рекомендация на неделю: правила, мало данных, пустое расписание (R-49)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from src.bot.access import OWNER_KEY
from src.bot.handlers import cmd_advice
from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.messages import format_advice
from src.core.plan import (
    MissedItem,
    MissReason,
    ScheduleAdvice,
    WeeklyReview,
    build_advice,
)
from src.core.schedule import Weekday, WorkoutView
from src.db.models import Workout
from tests.fakes import FakeBot, make_context, make_update

ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _review(total: int, done: int, misses: int, *, has_snapshots=True) -> WeeklyReview:
    return WeeklyReview(
        week_start=date(2026, 10, 26),
        week_end=date(2026, 11, 1),
        has_snapshots=has_snapshots,
        total=total,
        done=done,
        rest_days=0,
        misses=tuple(
            MissedItem(date(2026, 10, 26), "бег", MissReason.SKIPPED, None, None)
            for _ in range(misses)
        ),
        advice=ScheduleAdvice.NONE,
    )


def _view(duration: int, idx: int = 1) -> WorkoutView:
    return WorkoutView(
        id=idx,
        workout_type="бег",
        weekdays=(Weekday.MONDAY,),
        time_of_day=time(19, 0),
        duration_min=duration,
    )


def test_high_misses_reduce_count():
    reviews = [_review(3, 1, 2) for _ in range(4)]
    workouts = [_view(40, 1), _view(40, 2), _view(40, 3)]

    advice = build_advice(reviews, workouts)

    assert (advice.recommended_count, advice.recommended_volume) == (2, 40)
    assert advice.has_data is True


def test_rare_misses_keep_count():
    reviews = [
        _review(3, 3, 0),
        _review(3, 3, 0),
        _review(3, 3, 0),
        _review(3, 2, 1),
    ]

    advice = build_advice(reviews, [_view(40, 1), _view(40, 2), _view(40, 3)])

    assert advice.recommended_count == 3
    assert advice.misses == 1


def test_little_data_uses_current_schedule():
    reviews = [_review(3, 3, 0, has_snapshots=False)]

    advice = build_advice(reviews, [_view(45, 1)])

    assert advice.has_data is False
    assert advice.recommended_count == 1
    assert advice.recommended_volume == 45
    assert "Данных мало" in format_advice(advice)


def test_empty_schedule_starts_with_one():
    advice = build_advice([], [])

    assert advice.empty_schedule is True
    assert (advice.recommended_count, advice.recommended_volume) == (1, 30)


def test_volume_is_median():
    advice = build_advice(
        [_review(3, 3, 0) for _ in range(4)],
        [_view(20, 1), _view(40, 2), _view(45, 3)],
    )

    assert advice.recommended_volume == 40


def test_advice_text_shows_numbers_and_footer():
    advice = build_advice(
        [_review(3, 1, 2) for _ in range(4)],
        [_view(40, 1), _view(40, 2), _view(40, 3)],
    )

    text = format_advice(advice)

    assert "4 нед." in text
    assert "сделано 4 из 12" in text
    assert "Совет: 2 тренировки по 40 минут" in text
    assert "Расписание я не меняю" in text


# --- Сервис и команда ------------------------------------------------------


def _service(session_factory) -> WellnessService:
    return WellnessService(session_factory, ZONE, clock=lambda: NOW)


async def test_advice_without_data_says_current(session_factory):
    WorkoutService(session_factory, ZONE, clock=lambda: NOW).create_weekly(
        "бег", (Weekday.MONDAY,), time(19, 0), duration_min=40
    )

    text = _service(session_factory).advice()

    assert "Данных мало" in text
    assert "Сейчас: 1 тренировка" in text
    assert "Совет: 1 тренировка по 40 минут" in text


def test_advice_empty_schedule(session_factory):
    text = _service(session_factory).advice()

    assert "тренировок нет" in text
    assert "30 минут" in text


def test_advice_does_not_write(session_factory):
    WorkoutService(session_factory, ZONE, clock=lambda: NOW).create_weekly(
        "бег", (Weekday.MONDAY,), time(19, 0)
    )
    with session_factory() as session:
        before = session.scalar(select(func.count()).select_from(Workout))

    _service(session_factory).advice()

    with session_factory() as session:
        after = session.scalar(select(func.count()).select_from(Workout))
    assert before == after


async def test_advice_command_is_html_free_and_without_model(session_factory):
    wellness = _service(session_factory)
    bot = FakeBot()
    update = make_update("/advice")
    context = make_context(
        {"wellness_service": wellness, OWNER_KEY: 42}, bot=bot
    )

    await cmd_advice(update, context)

    assert "Рекомендация на неделю" in update.message.sent[0]
    assert bot.actions == []
