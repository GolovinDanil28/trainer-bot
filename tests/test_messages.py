"""Тесты текстов ответов: сравниваем готовые строки."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from src.core.messages import (
    EMPTY_LIST_HINT,
    format_created,
    format_disabled,
    format_draft_created,
    format_reminder,
    format_schedule,
    format_time,
    format_unknown_workout,
    format_weekdays,
)
from src.core.reminders import ReminderMessage
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


def test_reminder_text_uses_local_time():
    # 14:30 UTC = 19:30 в Екатеринбурге (UTC+5).
    message = ReminderMessage(
        reminder_id=5,
        workout_id=1,
        workout_type=WorkoutType.STRENGTH,
        duration_min=45,
        scheduled_at_utc=datetime(2026, 10, 26, 14, 30, tzinfo=UTC),
    )

    text = format_reminder(message, ZoneInfo("Asia/Yekaterinburg"))

    assert text == (
        "Пора тренироваться: силовая, 45 мин, 19:30.\n"
        "Отметь результат кнопкой ниже."
    )


def test_reminder_text_does_not_leak_ids():
    # Пользователю не нужны ни id напоминания, ни id тренировки.
    message = ReminderMessage(
        reminder_id=5,
        workout_id=77,
        workout_type=WorkoutType.CARDIO,
        duration_min=30,
        scheduled_at_utc=datetime(2026, 10, 26, 4, 0, tzinfo=UTC),
    )

    text = format_reminder(message, ZoneInfo("Asia/Yekaterinburg"))

    assert "77" not in text
    assert "#5" not in text


def test_reminder_text_for_long_workout_stays_readable():
    # Длительность не ограничена сверху: 120 минут не должны ломать строку
    # и не должны превращаться в «2 часа» — пользователь задавал минуты.
    message = ReminderMessage(
        reminder_id=9,
        workout_id=3,
        workout_type=WorkoutType.MOBILITY,
        duration_min=120,
        scheduled_at_utc=datetime(2026, 10, 26, 7, 0, tzinfo=UTC),
    )

    text = format_reminder(message, ZoneInfo("Europe/Moscow"))

    assert text == (
        "Пора тренироваться: мобильность, 120 мин, 10:00.\n"
        "Отметь результат кнопкой ниже."
    )
