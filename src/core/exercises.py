"""Упражнения для рекомендации: курируемые программы по семействам (R-50).

Никакой модели: набор фиксирован, его можно объяснить и проверить. Это общая
подсказка, а не медицинская рекомендация — текст об этом говорит прямо.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from src.core.schedule import WorkoutView

# Цикл программы: 8 недель, фазы по 2 недели.
CYCLE_WEEKS = 8
PHASE_WEEKS = 2


class Family(StrEnum):
    STRENGTH = "strength"
    CARDIO = "cardio"
    MOBILITY = "mobility"


FAMILY_LABELS: dict[Family, str] = {
    Family.STRENGTH: "силовая",
    Family.CARDIO: "кардио",
    Family.MOBILITY: "мобильность",
}

# Порядок блоков: сначала сила, потом кардио, потом мобильность.
FAMILY_ORDER: tuple[Family, ...] = (Family.STRENGTH, Family.CARDIO, Family.MOBILITY)

# Корни типов. Незнакомый тип не получает блок: пустота честнее выдумки.
_FAMILY_ROOTS: tuple[tuple[Family, tuple[str, ...]], ...] = (
    (Family.STRENGTH, ("силов", "жим", "присед", "тяга", "штанг", "гантел", "кроссфит", "отжим")),
    (Family.CARDIO, ("бег", "кардио", "вело", "плаван", "лыж", "ходьб", "интервал", "скакал")),
    (Family.MOBILITY, ("растяж", "мобильн", "йог", "пилатес", "гибкост")),
)

# Четыре фазы по две недели. Тексты с дозировкой — так их можно показать как есть.
PROGRAMS: dict[Family, tuple[tuple[str, ...], ...]] = {
    Family.STRENGTH: (
        ("приседания — 2×8", "тяга — 2×8", "жим — 2×8"),
        ("приседания — 3×8", "тяга — 3×8", "жим — 3×8"),
        ("приседания — 3×10", "тяга — 3×10", "жим — 3×10"),
        ("приседания — 4×8", "тяга — 4×8", "жим — 4×8", "планка — 3×40 сек"),
    ),
    Family.CARDIO: (
        ("20 минут ровным темпом",),
        ("3×(4 мин быстро / 2 мин спокойно)",),
        ("5×(3 мин быстро / 1 мин спокойно)",),
        ("25–30 минут ровным темпом",),
    ),
    Family.MOBILITY: (
        (
            "наклон вперёд — 30 сек",
            "вращения плеч — 30 сек",
            "раскрытие грудного отдела — 30 сек",
        ),
        (
            "наклон вперёд — 40 сек",
            "вращения плеч — 40 сек",
            "раскрытие грудного отдела — 40 сек",
            "растяжка сгибателей бедра — 40 сек",
        ),
        (
            "наклон вперёд — 45 сек",
            "вращения плеч — 45 сек",
            "раскрытие грудного отдела — 45 сек",
            "растяжка сгибателей бедра — 45 сек",
        ),
        (
            "наклон вперёд — 45 сек",
            "вращения плеч — 45 сек",
            "раскрытие грудного отдела — 45 сек",
            "растяжка сгибателей бедра — 45 сек",
            "скручивание лёжа — 45 сек",
        ),
    ),
}

DISCLAIMER = "Это общая подсказка, не медицинская рекомендация."


def classify(workout_type: str) -> Family | None:
    """Семейство по корню типа. `None` — тип не узнан."""
    value = workout_type.lower()
    for family, roots in _FAMILY_ROOTS:
        if any(root in value for root in roots):
            return family
    return None


@dataclass(frozen=True, slots=True)
class ExerciseBlock:
    """Один блок советов: семейство, неделя цикла и упражнения фазы."""

    family: Family
    label: str
    week: int
    exercises: tuple[str, ...]
    restarted: bool = False


def _cycle_week(created: date, today: date) -> tuple[int, bool]:
    """Неделя внутри цикла и признак «цикл завершён и начат заново»."""
    weeks = (today - created).days // 7 + 1
    if weeks <= CYCLE_WEEKS:
        return max(1, weeks), False
    week_in_cycle = (weeks - 1) % CYCLE_WEEKS + 1
    return week_in_cycle, True


def build_blocks(
    workouts: list[WorkoutView], today: date, zone: ZoneInfo
) -> tuple[ExerciseBlock, ...]:
    """Блоки по семействам текущих тренировок, один на семейство."""
    starts: dict[Family, date] = {}
    for workout in workouts:
        family = classify(workout.workout_type)
        if family is None or workout.created_at is None:
            continue
        created = workout.created_at.astimezone(zone).date()
        if family not in starts or created < starts[family]:
            starts[family] = created

    blocks: list[ExerciseBlock] = []
    for family in FAMILY_ORDER:
        if family not in starts:
            continue
        week, restarted = _cycle_week(starts[family], today)
        phase = (week - 1) // PHASE_WEEKS
        blocks.append(
            ExerciseBlock(
                family=family,
                label=FAMILY_LABELS[family],
                week=week,
                exercises=PROGRAMS[family][phase],
                restarted=restarted,
            )
        )
    return tuple(blocks)
