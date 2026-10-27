"""Правила плана на завтра: чистая функция и её тексты (R-23…R-25).

Таблица D6 из design.md — главный тест: если правило округления поменяют, здесь
начнёт падать конкретная строка, а не «где-то в плане».
"""

from datetime import time

import pytest

from src.core.messages import format_plan, format_plan_without_score
from src.core.plan import build_plan, reduced_duration
from src.core.schedule import Weekday, WorkoutType, WorkoutView
from src.core.wellness import WellnessScore


def make_view(
    duration_min: int = 45,
    workout_type: WorkoutType = WorkoutType.STRENGTH,
    time_of_day: time = time(19, 30),
) -> WorkoutView:
    return WorkoutView(
        id=1,
        workout_type=workout_type,
        weekdays=(Weekday.TUESDAY,),
        time_of_day=time_of_day,
        duration_min=duration_min,
    )


@pytest.mark.parametrize(
    ("duration_min", "expected"),
    [
        (45, 30),
        (60, 40),
        (20, 10),
        (10, 5),
        (5, 5),
        (90, 60),
        (30, 20),
    ],
    ids=["45", "60", "20", "10-минимум", "5-минимум", "90", "30"],
)
def test_reduced_duration_rounds_down_to_five(duration_min, expected):
    assert reduced_duration(duration_min) == expected


@pytest.mark.parametrize("score", [1, 2], ids=["один", "два"])
def test_low_score_means_rest(score):
    plan = build_plan(WellnessScore(score), [make_view()])

    assert plan.is_rest is True
    assert plan.items == ()
    assert plan.score == score


def test_light_score_reduces_but_keeps_workout():
    plan = build_plan(WellnessScore(3), [make_view(45)])

    assert plan.is_rest is False
    assert len(plan.items) == 1
    assert plan.items[0].duration_min == 30
    assert plan.items[0].original_duration_min == 45


@pytest.mark.parametrize("score", [4, 5], ids=["четыре", "пять"])
def test_high_score_keeps_schedule(score):
    plan = build_plan(WellnessScore(score), [make_view(45)])

    assert plan.items[0].duration_min == 45
    assert plan.items[0].original_duration_min == 45


def test_light_score_without_workouts_is_not_rest():
    # «Отдых» и «завтра нет тренировок» — разные ответы: первый — следствие
    # оценки, второй — расписания.
    plan = build_plan(WellnessScore(3), [])

    assert plan.is_rest is False
    assert plan.has_workouts is False


def test_rest_without_workouts_is_rest():
    plan = build_plan(WellnessScore(1), [])

    assert plan.is_rest is True
    assert plan.has_workouts is False


def test_plan_text_on_rest_mentions_reminder():
    # design D7: план не отменяет напоминание, поэтому текст обязан предупредить.
    text = format_plan(build_plan(WellnessScore(2), [make_view()]))

    assert text == (
        "Завтра отдых.\n"
        "Напоминание по расписанию всё равно придёт — отметь «пропустил», "
        "чтобы день попал в журнал."
    )


def test_plan_text_explains_reduction():
    text = format_plan(build_plan(WellnessScore(3), [make_view(45)]))

    assert "облегчённый" in text
    assert "силовая, 19:30, 30 мин" in text
    assert "обычно 45" in text


def test_plan_text_without_reduction_has_no_excuse():
    text = format_plan(build_plan(WellnessScore(4), [make_view(45)]))

    assert "силовая, 19:30, 45 мин" in text
    assert "обычно" not in text


def test_plan_shows_time_for_each_workout():
    plan = build_plan(
        WellnessScore(5),
        [
            make_view(45, WorkoutType.CARDIO, time(8, 0)),
            make_view(45, WorkoutType.STRENGTH, time(19, 30)),
        ],
    )

    text = format_plan(plan)

    assert "кардио, 08:00, 45 мин" in text
    assert "силовая, 19:30, 45 мин" in text


def test_plan_text_when_no_workouts_tomorrow():
    text = format_plan(build_plan(WellnessScore(4), []))

    assert text == (
        "Завтра тренировок нет. Можно отдохнуть или добавить тренировку: "
        "/new strength Пн,Ср,Пт 19:30"
    )


def test_plan_text_without_score_is_a_hint():
    text = format_plan_without_score()

    assert "не отмечено" in text
    assert "/wellness 3" in text
