"""Правила плана на завтра: оценка самочувствия + завтрашнее расписание.

Чистая функция: ни базы, ни Telegram. План — проекция двух фактов, поэтому его
можно посчитать в любой момент, не хранить и не инвалидировать. Отсюда же
следует R-24: план ничего не меняется, он только читает.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time

from src.core.schedule import WorkoutType, WorkoutView
from src.core.wellness import WellnessScore

# Оценка 1–2 — отдых: при плохом самочувствии тренировка не «чуть меньше», а
# отменяется планом. Напоминание при этом остаётся (R-24), см. текст в messages.
REST_MAX_SCORE = 2
# Оценка 3 — самый частый случай: тренировка остаётся, но легче.
LIGHT_SCORE = 3
REDUCTION_PERCENT = 30

# Шаг округления вниз. Пять минут — потому что «32 минуты» в плане читаются как
# ошибка, а не как правило. Нижняя граница — 5: облегчение не должно обнулять.
DURATION_STEP_MIN = 5
MIN_DURATION_MIN = 5

_ONE_HUNDRED_PERCENT = 100


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Одна строка плана: что делать, когда и сколько это займёт.

    `original_duration_min` хранится, чтобы текст мог объяснить поправку
    («30 мин, обычно 45»), не пересчитывая правило второй раз. Время
    переносится из расписания: R-24 требует, чтобы план отвечал «во сколько»,
    иначе две тренировки одного типа в разные часы выглядели бы одинаково.
    """

    workout_type: WorkoutType
    time_of_day: time
    duration_min: int
    original_duration_min: int


@dataclass(frozen=True, slots=True)
class DayPlan:
    """План на завтра. `is_rest` и пустой список — разные вещи."""

    score: int
    is_rest: bool
    items: tuple[PlanItem, ...]

    @property
    def has_workouts(self) -> bool:
        return bool(self.items)


def reduced_duration(duration_min: int) -> int:
    """70 % длительности, округлённое вниз до 5 минут, но не меньше 5.

    45 → 31 → 30; 60 → 42 → 40; 20 → 14 → 10; 10 → 7 → 5; 5 → 3 → 5.
    """
    kept_percent = _ONE_HUNDRED_PERCENT - REDUCTION_PERCENT
    reduced = duration_min * kept_percent // _ONE_HUNDRED_PERCENT
    return max(MIN_DURATION_MIN, reduced // DURATION_STEP_MIN * DURATION_STEP_MIN)


def build_plan(score: WellnessScore, workouts: Sequence[WorkoutView]) -> DayPlan:
    """Собрать план на завтра. Одинаковый вход — одинаковый план (R-23)."""
    if score.value <= REST_MAX_SCORE:
        return DayPlan(score=score.value, is_rest=True, items=())

    if score.value == LIGHT_SCORE:
        items = tuple(
            PlanItem(
                workout_type=workout.workout_type,
                time_of_day=workout.time_of_day,
                duration_min=reduced_duration(workout.duration_min),
                original_duration_min=workout.duration_min,
            )
            for workout in workouts
        )
    else:
        # 4–5: план совпадает с расписанием, включая длительность.
        items = tuple(
            PlanItem(
                workout_type=workout.workout_type,
                time_of_day=workout.time_of_day,
                duration_min=workout.duration_min,
                original_duration_min=workout.duration_min,
            )
            for workout in workouts
        )

    return DayPlan(score=score.value, is_rest=False, items=items)
