"""Тексты ответов пользователю.

Здесь только «данные → строка»: ни Telegram, ни базы. Поэтому тексты
проверяются обычным сравнением строк, а не через фейковый чат.
"""

from collections.abc import Sequence
from datetime import time
from zoneinfo import ZoneInfo

from src.core.help import COMMANDS, FREE_TEXT_EXAMPLES, HELP_TRIGGER_HINT
from src.core.history import HistoryEntry
from src.core.plan import (
    DURATION_STEP_MIN,
    LIGHT_SCORE,
    MIN_DURATION_MIN,
    REDUCTION_PERCENT,
    REST_MAX_SCORE,
    DayPlan,
    MissReason,
    ScheduleAdvice,
    WeeklyReview,
)
from src.core.reminders import CHECKIN_STATUS_LABELS, CheckinStatus, ReminderMessage
from src.core.schedule import (
    WEEKDAY_SHORT,
    Weekday,
    WorkoutView,
    workout_label,
)

EMPTY_LIST_HINT = "Пока нет тренировок.\nСоздай первую: /new strength Пн,Ср,Пт 19:30"


def format_weekdays(days: Sequence[Weekday]) -> str:
    return ", ".join(WEEKDAY_SHORT[day] for day in days)


def format_time(moment: time) -> str:
    return moment.strftime("%H:%M")


def format_volume(workout: WorkoutView) -> str:
    """Длительность в человеческом виде: дистанция + примерные минуты (R-32)."""
    if workout.distance_km is None:
        return f"{workout.duration_min} мин"
    km = f"{workout.distance_km:g} км"
    return f"{km} (≈{workout.duration_min} мин)"


def format_when(workout: WorkoutView) -> str:
    """Когда тренировка: день недели у недельной, дата у разовой (R-35)."""
    if workout.date_once is not None:
        return workout.date_once.strftime("%d.%m")
    return format_weekdays(workout.weekdays)


def _line(workout: WorkoutView) -> str:
    return (
        f"#{workout.id} — {workout_label(workout.workout_type)}, "
        f"{format_when(workout)}, "
        f"{format_time(workout.time_of_day)} ({format_volume(workout)})"
    )


def format_created(workout: WorkoutView) -> str:
    """Подтверждение после `/new` (R-01)."""
    return f"Готово: {_line(workout)}."


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
        workout = workout_label(entry.workout_type)
        lines.append(f"{moment:%d.%m.%Y %H:%M} — {workout}, {result}")
    return "\n".join(lines)


def format_reminder(message: ReminderMessage, zone: ZoneInfo) -> str:
    """Текст напоминания (R-07): тип, длительность и время в таймзоне пользователя."""
    local_time = message.scheduled_at_utc.astimezone(zone).strftime("%H:%M")
    return (
        f"Пора тренироваться: {workout_label(message.workout_type)}, "
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


# --- справка (R-28…R-31) --------------------------------------------------

# Правила собираются из констант плана, а не пишутся текстом: иначе изменение
# правила в `core/plan.py` оставит карточку врать — молча.
HELP_PLAN_RULES = (
    f"Правила плана: 1–{REST_MAX_SCORE} — отдых, {LIGHT_SCORE} — "
    f"{100 - REDUCTION_PERCENT} % длительности "
    f"(вниз до {DURATION_STEP_MIN} минут, но не меньше {MIN_DURATION_MIN}), "
    f"{LIGHT_SCORE + 1}–5 — как в расписании.\n"
    "План не отменяет напоминание: при отдыхе отметь его «пропустил»."
)

HELP_LIMITS = (
    "Чего не умею:\n"
    "— разовые переносы («завтра не смогу»);\n"
    "— замену одной тренировки другой;\n"
    "— несколько действий в одной фразе;\n"
    "— составление плана на следующую неделю."
)


def format_help() -> str:
    """Карточка возможностей (R-28).

    Строится из `COMMANDS`, а не пишется строкой: иначе новая команда появится
    в боте и исчезнет из справки — молча.
    """
    lines = ["Что я умею", ""]
    triggers = ", ".join(f"«{trigger}»" for trigger in HELP_TRIGGER_HINT)
    lines.append(f"Справку можно попросить словами: {triggers}.")
    lines.extend(["", "Команды:"])
    for entry in COMMANDS:
        lines.append(f"{entry.command} — {entry.purpose}")
        # Пример печатаем только когда он добавляет информацию: у шести команд
        # из восьми example совпадает с командой, и вторая строка — шум.
        if entry.example != entry.command:
            lines.append(f"  {entry.example}")

    lines.extend(["", "Можно фразой:"])
    for phrase, meaning in FREE_TEXT_EXAMPLES:
        lines.append(f"  «{phrase}» → {meaning}")

    lines.extend(["", HELP_PLAN_RULES, "", HELP_LIMITS])
    return "\n".join(lines)


def format_start() -> str:
    """/start: приветствие и та же карточка, что у /help (R-28)."""
    return (
        "Привет! Я напоминаю о тренировках, веду журнал и собираю план "
        "по самочувствию.\n\n" + format_help()
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
            f"— {workout_label(item.workout_type)}, "
            f"{format_time(item.time_of_day)}, {item.duration_min} мин"
        )
        if item.duration_min != item.original_duration_min:
            line += f" (обычно {item.original_duration_min})"
        lines.append(line)
    return "\n".join(lines)

# --- уточнение типа (R-34) ------------------------------------------------


def format_type_choice(options: tuple[str, ...]) -> str:
    """Вопрос вместо создания: тип не выбран, БД не тронута."""
    return (
        "Уточни, какую тренировку создать: " + " или ".join(options) + ".\n"
        "Выбери кнопкой ниже."
    )


CHOICE_EXPIRED_TEXT = (
    "Уточнение устарело, ничего не создал. Сформулируй фразу заново — "
    "одним сообщением."
)
CHOICE_DONE_TEXT = "Эту тренировку уже создал — второй не будет."


def format_date_unclear(raw: str) -> str:
    """Дата не распознана или уже прошла: подсказка, а не молчание (R-35)."""
    return (
        f"Не понял дату «{raw}». Назови «сегодня», «завтра» или «28.10» — "
        "и не раньше сегодняшнего дня."
    )


# --- недельный разбор (R-37, R-38) ---------------------------------------


MISS_REASON_LABELS: dict[MissReason, str] = {
    MissReason.SKIPPED: "пропустил",
    MissReason.MISSED: "не ответил до конца дня",
    MissReason.NO_ANSWER: "без отметки",
    MissReason.NO_REMINDER: "напоминания не было",
}

ADVICE_TEXTS: dict[ScheduleAdvice, str] = {
    ScheduleAdvice.ADD_WORKOUT: (
        "Всё, что советовал план, сделано. Предложение: добавить одну тренировку "
        "на следующую неделю."
    ),
    ScheduleAdvice.REDUCE_OR_MORNING: (
        "Пропусков два и больше при низком самочувствии. Предложение: сократить "
        "число тренировок или перенести их на утро."
    ),
    ScheduleAdvice.REDUCE: (
        "Пропущено больше половины плана. Предложение: убрать одну тренировку, "
        "чтобы неделя стала выполнимой."
    ),
    ScheduleAdvice.NONE: (
        "Расписание менять не нужно: эта неделя не даёт повода."
    ),
}

WEEK_SCHEDULE_FOOTER = (
    "Расписание я не меняю. Если решишь — /off и /new."
)


def plural_workouts(count: int) -> str:
    """Родительный падеж после «из»: «из 1 тренировки», «из 2 тренировок»."""
    if count % 10 == 1 and count % 100 != 11:
        return "тренировки"
    return "тренировок"


def plural_days(count: int) -> str:
    """«1 день», «2 дня», «5 дней» — для строки про отдых."""
    if count % 10 == 1 and count % 100 != 11:
        return "день"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "дня"
    return "дней"


def format_week_range(review: WeeklyReview) -> str:
    return f"{review.week_start:%d.%m}–{review.week_end:%d.%m}"


def format_weekly_review(review: WeeklyReview) -> str:
    """Итог недели, причины пропусков и предложение (R-37, R-38)."""
    header = f"Разбор недели {format_week_range(review)}"

    if not review.has_snapshots:
        return (
            f"{header}\n"
            "Планов за эту неделю нет: я показываю план после вечернего вопроса, "
            "а ответов не было. Сравнивать нечего."
        )

    if review.total == 0:
        # Отдых и «в плане не было тренировок» — разные факты: во втором случае
        # бот ничего не советовал, и говорить про отдых нельзя.
        if review.rest_days:
            lines = [
                header,
                f"Бот советовал отдых "
                f"{review.rest_days} {plural_days(review.rest_days)}.",
                "",
                ADVICE_TEXTS[review.advice],
                WEEK_SCHEDULE_FOOTER,
            ]
        else:
            lines = [
                header,
                "Планов с тренировками не было: в расписании на эти дни "
                "ничего не стояло.",
                "",
                ADVICE_TEXTS[review.advice],
                WEEK_SCHEDULE_FOOTER,
            ]
        return "\n".join(lines)

    lines = [
        header,
        f"Сделано {review.done} из {review.total} {plural_workouts(review.total)}.",
    ]
    if review.rest_days:
        lines.append(
            f"Бот советовал отдых {review.rest_days} "
            f"{plural_days(review.rest_days)}."
        )

    lines.extend(["", "Что мешало:"])
    if not review.misses:
        lines.append("— пропусков нет, всё по плану.")
    else:
        for miss in review.misses:
            day = f"{WEEKDAY_SHORT[Weekday(miss.day.weekday())].lower()} {miss.day:%d.%m}"
            line = (
                f"— {day}: {workout_label(miss.workout_type)} — "
                f"{MISS_REASON_LABELS[miss.reason]}"
            )
            if miss.score is not None:
                line += f"; самочувствие {miss.score}/5"
                if miss.note:
                    line += f" «{miss.note}»"
            lines.append(line)

    lines.extend(["", ADVICE_TEXTS[review.advice], WEEK_SCHEDULE_FOOTER])
    return "\n".join(lines)
