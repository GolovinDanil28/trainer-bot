"""Правила плана на завтра и недельный разбор.

`build_plan`/`build_weekly_review` — чистые функции: ни базы, ни Telegram.
План — проекция расписания и оценки, его можно посчитать в любой момент.
Хранится не план, а **снимок показанного плана** (R-36): его пишет bot-слой,
потому что именно он знает, что план действительно ушёл владельцу. R-24 при
этом не нарушается: снимок не трогает расписание, тренировки и напоминания.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from src.core.schedule import (
    DURATION_STEP_MIN,
    MIN_DURATION_MIN,
    WorkoutView,
)
from src.core.wellness import WellnessEntry, WellnessScore

# Оценка 1–2 — отдых: при плохом самочувствии тренировка не «чуть меньше», а
# отменяется планом. Напоминание при этом остаётся (R-24), см. текст в messages.
REST_MAX_SCORE = 2
# Оценка 3 — самый частый случай: тренировка остаётся, но легче.
LIGHT_SCORE = 3
REDUCTION_PERCENT = 30

_ONE_HUNDRED_PERCENT = 100


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Одна строка плана: что делать, когда и сколько это займёт.

    `original_duration_min` хранится, чтобы текст мог объяснить поправку
    («30 мин, обычно 45»), не пересчитывая правило второй раз. Время
    переносится из расписания: R-24 требует, чтобы план отвечал «во сколько»,
    иначе две тренировки одного типа в разные часы выглядели бы одинаково.
    """

    workout_type: str
    time_of_day: time
    duration_min: int
    original_duration_min: int
    # Нужен снимку (R-36): по нему элемент плана сопоставляется с отметкой.
    workout_id: int | None = None
    # Дистанция только для показа (R-39): правило плана считается в минутах.
    distance_km: float | None = None


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
                workout_id=workout.id,
                distance_km=workout.distance_km,
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
                workout_id=workout.id,
                distance_km=workout.distance_km,
            )
            for workout in workouts
        )

    return DayPlan(score=score.value, is_rest=False, items=items)


# --- недельный разбор (R-37, R-38) ----------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotItem:
    """Элемент показанного плана: ровно то, что владелец видел."""

    workout_type: str
    time_of_day: time
    duration_min: int
    workout_id: int | None


@dataclass(frozen=True, slots=True)
class PlanSnapshotView:
    """Снимок плана на дату (R-36), прочитанный для разбора."""

    plan_date: date
    score: int
    is_rest: bool
    items: tuple[SnapshotItem, ...]


@dataclass(frozen=True, slots=True)
class WeekReminder:
    """Напоминание недели: с чем сравнивать элемент снимка."""

    workout_id: int
    scheduled_at_utc: datetime
    status: str
    checkin: str | None


class MissReason(StrEnum):
    """Почему элемент плана не сделан. Причина, а не интерпретация."""

    SKIPPED = "skipped"          # владелец нажал «пропустил»
    MISSED = "missed"            # не ответил до конца дня
    NO_ANSWER = "no_answer"      # напоминание ушло, отметки нет
    NO_REMINDER = "no_reminder"  # элемента плана не нашлось в напоминаниях


class ScheduleAdvice(StrEnum):
    """Предложение по следующей неделе (R-38). Текст живёт в messages.py."""

    ADD_WORKOUT = "add"
    REDUCE_OR_MORNING = "reduce_or_morning"
    REDUCE = "reduce"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class MissedItem:
    """Пропущенный элемент плана вместе с самочувствием того дня."""

    day: date
    workout_type: str
    reason: MissReason
    score: int | None
    note: str | None


@dataclass(frozen=True, slots=True)
class WeeklyReview:
    """Итог недели: факты (M, N, пропуски) и одно предложение."""

    week_start: date
    week_end: date
    has_snapshots: bool
    total: int
    done: int
    rest_days: int
    misses: tuple[MissedItem, ...]
    advice: ScheduleAdvice
    # Элементы, отсечённые `until`: время ещё не наступило. Отдельно от
    # «в плане ничего не стояло» — иначе разбор врёт (найдено ревью).
    upcoming: int = 0


def week_window(today: date) -> tuple[date, date]:
    """Последняя завершённая календарная неделя: пн–вс (D3)."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def current_week(today: date) -> tuple[date, date]:
    """Текущая неделя пн–вс, включая сегодня: для воскресного разбора (R-40)."""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


# Порог «низкого самочувствия» в совете: средняя оценка по дням пропусков.
LOW_WELLNESS_MAX = 2.5
# Сколько пропусков делают совет про нагрузку осмысленным.
MIN_MISSES_FOR_ADVICE = 2
# Столько выполненных элементов нужно, чтобы советовать добавку.
MIN_DONE_FOR_ADD = 3


def _miss_reason(reminder: WeekReminder | None) -> MissReason:
    if reminder is None:
        return MissReason.NO_REMINDER
    if reminder.checkin is not None:
        return MissReason.SKIPPED if reminder.checkin == "skipped" else MissReason.NO_ANSWER
    if reminder.status == "missed":
        return MissReason.MISSED
    return MissReason.NO_ANSWER


def build_weekly_review(
    snapshots: Sequence[PlanSnapshotView],
    reminders: Sequence[WeekReminder],
    wellness: Mapping[date, WellnessEntry],
    week_start: date,
    week_end: date,
    zone: ZoneInfo,
    until: datetime | None = None,
) -> WeeklyReview:
    """Собрать разбор недели. Одинаковый вход — одинаковый разбор (R-37).

    `until` — момент, позже которого элементы ещё не наступили: воскресный
    разбор в 20:00 не должен объявлять пропущенной тренировку в 21:00.
    Для `/week` (`until=None`) учитывается вся неделя.
    """
    by_key: dict[tuple[int, date, str], WeekReminder] = {}
    for reminder in reminders:
        local = reminder.scheduled_at_utc.astimezone(zone)
        by_key[(reminder.workout_id, local.date(), local.strftime("%H:%M"))] = reminder

    total = done = rest_days = upcoming = 0
    misses: list[MissedItem] = []
    in_week = [
        snapshot
        for snapshot in snapshots
        if week_start <= snapshot.plan_date <= week_end
    ]

    for snapshot in in_week:
        if snapshot.is_rest:
            rest_days += 1
            continue
        for item in snapshot.items:
            if until is not None:
                moment = datetime.combine(
                    snapshot.plan_date, item.time_of_day, tzinfo=zone
                )
                if moment > until:
                    # Ещё не наступило — не «сделано» и не «пропущено»,
                    # но и не «в плане ничего не было».
                    upcoming += 1
                    continue
            total += 1
            key = (
                item.workout_id,
                snapshot.plan_date,
                item.time_of_day.strftime("%H:%M"),
            )
            reminder = by_key.get(key) if item.workout_id is not None else None
            if reminder is not None and reminder.checkin == "done":
                done += 1
                continue
            entry = wellness.get(snapshot.plan_date)
            misses.append(
                MissedItem(
                    day=snapshot.plan_date,
                    workout_type=item.workout_type,
                    reason=_miss_reason(reminder),
                    score=None if entry is None else entry.score,
                    note=None if entry is None else entry.note,
                )
            )

    return WeeklyReview(
        week_start=week_start,
        week_end=week_end,
        has_snapshots=bool(in_week),
        total=total,
        done=done,
        rest_days=rest_days,
        misses=tuple(misses),
        advice=_advice(total, done, misses),
        upcoming=upcoming,
    )


def _advice(
    total: int, done: int, misses: Sequence[MissedItem]
) -> ScheduleAdvice:
    """Одно предложение по приоритету правил (D5)."""
    if total == 0:
        return ScheduleAdvice.NONE
    if total >= MIN_DONE_FOR_ADD and done == total:
        return ScheduleAdvice.ADD_WORKOUT

    # Средняя — по дням, а не по элементам: день с тремя пропусками иначе весит
    # втрое. Если оценок нет ни за один день пропусков, правило не применяется:
    # догадка «наверное, плохо себя чувствовал» — не факт.
    by_day = {miss.day: miss.score for miss in misses if miss.score is not None}
    low_wellness = bool(by_day) and (
        sum(by_day.values()) / len(by_day)
    ) <= LOW_WELLNESS_MAX
    if len(misses) >= MIN_MISSES_FOR_ADVICE and low_wellness:
        return ScheduleAdvice.REDUCE_OR_MORNING
    # M = 1 и N = 0 — это не «перегруз», а одна несостоявшаяся тренировка:
    # совет «убрать одну» звучал бы насмешкой.
    if total >= 2 and done * 2 < total:
        return ScheduleAdvice.REDUCE
    return ScheduleAdvice.NONE
