"""Тексты ответов пользователю.

Здесь только «данные → строка»: ни Telegram, ни базы. Поэтому тексты
проверяются обычным сравнением строк, а не через фейковый чат.
"""

from collections.abc import Sequence
from datetime import time

from src.core.schedule import (
    WEEKDAY_SHORT,
    WORKOUT_TYPE_LABELS,
    Weekday,
    WorkoutDraft,
    WorkoutView,
)

EMPTY_LIST_HINT = "Пока нет тренировок.\nСоздай первую: /new strength Пн,Ср,Пт 19:30"


def format_weekdays(days: Sequence[Weekday]) -> str:
    return ", ".join(WEEKDAY_SHORT[day] for day in days)


def format_time(moment: time) -> str:
    return moment.strftime("%H:%M")


def _line(workout: WorkoutView) -> str:
    return (
        f"#{workout.id} — {WORKOUT_TYPE_LABELS[workout.workout_type]}, "
        f"{format_weekdays(workout.weekdays)}, "
        f"{format_time(workout.time_of_day)} ({workout.duration_min} мин)"
    )


def format_created(workout: WorkoutView) -> str:
    """Подтверждение после `/new` (R-01)."""
    return f"Готово: {_line(workout)}."


def format_draft_created(workout_id: int, draft: WorkoutDraft) -> str:
    """Подтверждение, когда тренировка только что создана и known id."""
    return format_created(
        WorkoutView(
            id=workout_id,
            workout_type=draft.workout_type,
            weekdays=draft.weekdays,
            time_of_day=draft.time_of_day,
            duration_min=draft.duration_min,
        )
    )


def format_schedule(workouts: Sequence[WorkoutView]) -> str:
    """Список активных тренировок для `/list` (R-02)."""
    if not workouts:
        return EMPTY_LIST_HINT

    lines = [_line(workout) for workout in workouts]
    return "Твои тренировки:\n" + "\n".join(lines)


def format_disabled(workout_id: int) -> str:
    """Ответ на успешный `/off` (R-03)."""
    return f"Тренировка #{workout_id} отключена. История отметок сохранена."


def format_unknown_workout(workout_id: int) -> str:
    """Ответ на `/off` с несуществующим id (R-03)."""
    return f"Тренировки #{workout_id} нет. Посмотреть список: /list"
