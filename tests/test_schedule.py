"""Тесты разбора `/new`: без Telegram, только строка на входе."""

from datetime import time

import pytest

from src.core.schedule import (
    CommandError,
    Weekday,
    WorkoutType,
    parse_new_command,
)


def test_valid_command():
    draft = parse_new_command("/new strength Пн,Ср,Пт 19:30")

    assert draft.workout_type is WorkoutType.STRENGTH
    assert draft.weekdays == (Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY)
    assert draft.time_of_day == time(19, 30)
    assert draft.duration_min == 45


def test_command_is_case_insensitive_and_allows_single_digit_hour():
    draft = parse_new_command("/new CARDIO пн,пт 7:05")

    assert draft.workout_type is WorkoutType.CARDIO
    assert draft.weekdays == (Weekday.MONDAY, Weekday.FRIDAY)
    assert draft.time_of_day == time(7, 5)
    assert draft.duration_min == 30


def test_bot_mention_in_command_is_ignored():
    draft = parse_new_command("/new@TrainerBot mobility сб 10:00")

    assert draft.workout_type is WorkoutType.MOBILITY
    assert draft.weekdays == (Weekday.SATURDAY,)


def test_duplicate_days_are_merged_and_order_is_stable():
    first = parse_new_command("/new strength Пт,пн,Ср 19:30")
    second = parse_new_command("/new strength Пн,Ср,Пт 19:30")

    assert first.weekdays == second.weekdays == (
        Weekday.MONDAY,
        Weekday.WEDNESDAY,
        Weekday.FRIDAY,
    )


def test_time_out_of_range_is_rejected_with_example():
    with pytest.raises(CommandError, match="вне диапазона"):
        parse_new_command("/new strength Пн 25:00")


def test_bad_time_format_is_rejected():
    with pytest.raises(CommandError, match="формате ЧЧ:ММ"):
        parse_new_command("/new strength Пн 19-30")


def test_unknown_weekday_is_rejected_with_allowed_list():
    with pytest.raises(CommandError, match="Неизвестный день") as exc:
        parse_new_command("/new cardio Пн,Сббота 08:00")

    assert "пн" in str(exc.value)


def test_empty_day_in_list_is_rejected():
    with pytest.raises(CommandError, match="Пустой день"):
        parse_new_command("/new strength Пн,,Ср 19:30")


def test_unknown_type_is_rejected_with_allowed_list():
    with pytest.raises(CommandError, match="Неизвестный тип") as exc:
        parse_new_command("/new yoga Пн 19:30")

    assert "strength" in str(exc.value)


def test_wrong_argument_count_is_rejected():
    with pytest.raises(CommandError, match="три аргумента"):
        parse_new_command("/new strength Пн 19:30 extra")


def test_other_command_is_rejected():
    with pytest.raises(CommandError, match="Ожидалась команда /new"):
        parse_new_command("/list")


def test_empty_text_is_rejected():
    with pytest.raises(CommandError, match="Пустая команда"):
        parse_new_command("   ")
