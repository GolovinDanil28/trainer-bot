"""Тесты текстов ответов: сравниваем готовые строки."""

from datetime import time

from src.core.messages import (
    EMPTY_LIST_HINT,
    format_created,
    format_disabled,
    format_draft_created,
    format_schedule,
    format_time,
    format_unknown_workout,
    format_weekdays,
)
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType, WorkoutView

STRENGTH = WorkoutView(
    id=1,
    workout_type=WorkoutType.STRENGTH,
    weekdays=(Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY),
    time_of_day=time(19, 30),
    duration_min=45,
)
CARDIO = WorkoutView(
    id=2,
    workout_type=WorkoutType.CARDIO,
    weekdays=(Weekday.TUESDAY, Weekday.SATURDAY),
    time_of_day=time(7, 0),
    duration_min=30,
)


def test_weekdays_and_time_formatting():
    assert format_weekdays(STRENGTH.weekdays) == "Пн, Ср, Пт"
    assert format_time(time(7, 5)) == "07:05"
    assert format_time(time(19, 30)) == "19:30"


def test_created_message():
    assert format_created(STRENGTH) == "Готово: #1 — силовая, Пн, Ср, Пт, 19:30 (45 мин)."


def test_created_message_from_draft():
    draft = WorkoutDraft(
        workout_type=WorkoutType.MOBILITY,
        weekdays=(Weekday.SUNDAY,),
        time_of_day=time(10, 0),
        duration_min=20,
    )

    assert format_draft_created(7, draft) == (
        "Готово: #7 — мобильность, Вс, 10:00 (20 мин)."
    )


def test_schedule_with_two_workouts():
    text = format_schedule([STRENGTH, CARDIO])

    assert text == (
        "Твои тренировки:\n"
        "#1 — силовая, Пн, Ср, Пт, 19:30 (45 мин)\n"
        "#2 — кардио, Вт, Сб, 07:00 (30 мин)"
    )


def test_empty_schedule_points_to_the_command():
    assert format_schedule([]) == EMPTY_LIST_HINT
    assert "/new" in EMPTY_LIST_HINT


def test_disabled_and_unknown_messages():
    assert format_disabled(3) == "Тренировка #3 отключена. История отметок сохранена."
    assert format_unknown_workout(99) == "Тренировки #99 нет. Посмотреть список: /list"
