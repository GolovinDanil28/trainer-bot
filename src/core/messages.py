"""Тексты ответов пользователю.

Здесь только «данные → строка»: ни Telegram, ни базы. Поэтому тексты
проверяются обычным сравнением строк, а не через фейковый чат.
"""

from collections.abc import Sequence
from datetime import time
from zoneinfo import ZoneInfo

from src.core.history import HistoryEntry
from src.core.plan import DayPlan
from src.core.reminders import CHECKIN_STATUS_LABELS, CheckinStatus, ReminderMessage
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


EMPTY_HISTORY_HINT = (
    "История пока пуста. Первая запись появится после напоминания: "
    "отметь его кнопками «сделал» или «пропустил»."
)


def format_history(entries: Sequence[HistoryEntry], zone: ZoneInfo) -> str:
    """Последние записи журнала: дата, тип, результат (R-13).

    Дата — в таймзоне пользователя: в базе момент хранится в UTC, но человеку
    он нужен в своём времени, иначе «вчерашняя» тренировка выглядит сегодняшней.
    """
    if not entries:
        return EMPTY_HISTORY_HINT

    lines = ["Последние записи:"]
    for entry in entries:
        moment = entry.scheduled_at_utc.astimezone(zone)
        result = (
            "без ответа"
            if entry.checkin is None
            else CHECKIN_STATUS_LABELS[entry.checkin]
        )
        workout = WORKOUT_TYPE_LABELS[entry.workout_type]
        lines.append(f"{moment:%d.%m.%Y %H:%M} — {workout}, {result}")
    return "\n".join(lines)


def format_reminder(message: ReminderMessage, zone: ZoneInfo) -> str:
    """Текст напоминания (R-07): тип, длительность и время в таймзоне пользователя."""
    local_time = message.scheduled_at_utc.astimezone(zone).strftime("%H:%M")
    return (
        f"Пора тренироваться: {WORKOUT_TYPE_LABELS[message.workout_type]}, "
        f"{message.duration_min} мин, {local_time}.\n"
        "Отметь результат кнопкой ниже."
    )


def format_checkin_saved(status: CheckinStatus) -> str:
    """Подтверждение отметки (R-11)."""
    return f"Записал: {CHECKIN_STATUS_LABELS[status]}."


def format_already_marked(status: CheckinStatus) -> str:
    """Ответ на повторное нажатие: первая отметка не перезаписывается (R-12)."""
    return f"Уже отмечено: {CHECKIN_STATUS_LABELS[status]}. Изменить нельзя."


def format_unknown_reminder() -> str:
    """Кнопка от старого или удалённого напоминания."""
    return "Это напоминание больше не активно."


ASSISTANT_EXAMPLE = "/new strength Пн,Ср,Пт 19:30"


def format_assistant_fallback() -> str:
    """Сбой модели или невалидный ответ: ничего не изменили, дали пример (R-15)."""
    return (
        "Не разобрал фразу, поэтому ничего не изменил.\n"
        f"Пример: {ASSISTANT_EXAMPLE}\n"
        "Посмотреть расписание: /list"
    )


def format_assistant_unclear() -> str:
    """Отказ модели на фразу не про недельное расписание (R-14, R-16)."""
    return (
        "Это не про недельное расписание, поэтому ничего не меняю.\n"
        "Понимаю фразы вроде: «кардио по вторникам и четвергам в восемь утра», "
        "«покажи расписание», «убери вторую тренировку».\n"
        "Строгие команды: /new, /list, /off."
    )


# --- самочувствие и план (R-19…R-26) -------------------------------------


def format_wellness_prompt() -> str:
    """Вечерний вопрос о самочувствии (R-19)."""
    return "Как самочувствие сегодня? Оцени от 1 до 5."


def format_wellness_saved(score: int) -> str:
    """Подтверждение оценки; план добавляет сервис (R-20, R-21)."""
    return f"Записал самочувствие: {score}/5."


def format_wellness_command_hint() -> str:
    """`/wellness` без аргумента или с мусором (R-21)."""
    return (
        "Нужна оценка от 1 до 5. Пример: /wellness 3\n"
        "С заметкой: /wellness 2 не выспался"
    )


def format_stale_wellness_button() -> str:
    """Кнопка от вопроса другого дня (R-20)."""
    return (
        "Этот вопрос уже неактуален.\n"
        "Отметь самочувствие за сегодня командой /wellness, например /wellness 3"
    )


PLAN_WITHOUT_SCORE = (
    "Плана на завтра нет: самочувствие за сегодня не отмечено.\n"
    "Поставь оценку: /wellness 3"
)


def format_plan_without_score() -> str:
    """`/plan` без оценки (R-25)."""
    return PLAN_WITHOUT_SCORE


def format_plan(plan: DayPlan) -> str:
    """План на завтра (R-23, R-25).

    При отдыхе текст обязан сказать про напоминание: расписание read-only, и
    человек не должен ждать, что бот молча отменит тренировку (design D7).
    """
    if plan.is_rest:
        return (
            "Завтра отдых.\n"
            "Напоминание по расписанию всё равно придёт — отметь «пропустил», "
            "чтобы день попал в журнал."
        )

    if not plan.has_workouts:
        return (
            "Завтра тренировок нет. Можно отдохнуть или добавить тренировку: "
            f"{ASSISTANT_EXAMPLE}"
        )

    header = f"План на завтра (самочувствие {plan.score}/5):"
    if any(item.duration_min != item.original_duration_min for item in plan.items):
        header = f"План на завтра (самочувствие {plan.score}/5, облегчённый):"

    lines = [header]
    for item in plan.items:
        line = (
            f"— {WORKOUT_TYPE_LABELS[item.workout_type]}, "
            f"{format_time(item.time_of_day)}, {item.duration_min} мин"
        )
        if item.duration_min != item.original_duration_min:
            line += f" (обычно {item.original_duration_min})"
        lines.append(line)
    return "\n".join(lines)
