"""Упражнения в /advice: семейства, фазы, перезапуск цикла (R-50)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.exercises import (
    DISCLAIMER,
    Family,
    build_blocks,
    classify,
)
from src.core.messages import format_advice
from src.core.plan import build_advice
from src.core.schedule import Weekday, WorkoutView

ZONE = ZoneInfo("UTC")
TODAY = date(2026, 11, 5)


def _view(workout_type: str, days_ago: int = 0, idx: int = 1) -> WorkoutView:
    return WorkoutView(
        id=idx,
        workout_type=workout_type,
        weekdays=(Weekday.MONDAY,),
        time_of_day=time(19, 0),
        duration_min=40,
        created_at=datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        - timedelta(days=days_ago),
    )


# --- Классификация ---------------------------------------------------------


def test_classify_families():
    assert classify("силовая") is Family.STRENGTH
    assert classify("жим лёжа") is Family.STRENGTH
    assert classify("интервальный бег") is Family.CARDIO
    assert classify("велотренажёр") is Family.CARDIO
    assert classify("растяжка") is Family.MOBILITY
    assert classify("йога") is Family.MOBILITY


def test_unknown_type_has_no_family():
    assert classify("бокс") is None


# --- Фазы ------------------------------------------------------------------


def test_first_phase():
    blocks = build_blocks([_view("силовая", days_ago=10)], TODAY, ZONE)

    assert len(blocks) == 1
    assert (blocks[0].week, blocks[0].restarted) == (2, False)
    assert blocks[0].exercises[0].startswith("приседания")


def test_third_phase():
    blocks = build_blocks([_view("силовая", days_ago=35)], TODAY, ZONE)

    assert blocks[0].week == 6
    assert "3×10" in blocks[0].exercises[0]


def test_cycle_restarts_after_eight_weeks():
    blocks = build_blocks([_view("силовая", days_ago=56)], TODAY, ZONE)

    assert blocks[0].restarted is True
    assert blocks[0].week == 1


def test_one_block_per_family():
    workouts = [
        _view("силовая", idx=1),
        _view("силовая", idx=2),
        _view("бег", idx=3),
        _view("бокс", idx=4),
    ]

    blocks = build_blocks(workouts, TODAY, ZONE)

    assert [block.family for block in blocks] == [Family.STRENGTH, Family.CARDIO]


def test_empty_workouts_no_blocks():
    assert build_blocks([], TODAY, ZONE) == ()


# --- Текст -----------------------------------------------------------------


def test_advice_text_contains_exercises_and_disclaimer():
    blocks = build_blocks([_view("силовая", days_ago=10)], TODAY, ZONE)
    advice = build_advice([], [_view("силовая", days_ago=10)])

    text = format_advice(advice, blocks)

    assert "Упражнения:" in text
    assert "силовая, неделя 2 из 8" in text
    assert "приседания" in text
    assert DISCLAIMER in text


def test_no_blocks_no_disclaimer():
    advice = build_advice([], [_view("бокс")])

    text = format_advice(advice, ())

    assert "Упражнения" not in text
    assert DISCLAIMER not in text


# --- Сервис ----------------------------------------------------------------


def _service(session_factory) -> WellnessService:
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    return WellnessService(session_factory, ZONE, clock=lambda: now)


async def test_advice_service_includes_own_blocks(session_factory):
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    workouts = WorkoutService(session_factory, ZONE, clock=lambda: now)
    workouts.create_weekly("силовая", (Weekday.MONDAY,), time(19, 0), duration_min=40)
    workouts.create_weekly("бег", (Weekday.WEDNESDAY,), time(8, 0), duration_min=30)

    text = _service(session_factory).advice()

    assert "силовая, неделя 1 из 8" in text
    assert "кардио, неделя 1 из 8" in text
    assert DISCLAIMER in text


def test_advice_service_skips_unknown_type(session_factory):
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    WorkoutService(session_factory, ZONE, clock=lambda: now).create_weekly(
        "бокс", (Weekday.MONDAY,), time(19, 0)
    )

    text = _service(session_factory).advice()

    assert "Упражнения" not in text
