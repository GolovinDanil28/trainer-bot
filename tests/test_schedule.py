"""Тесты разбора `/new`: без Telegram, только строка на входе."""

from datetime import time

import pytest

from src.core.schedule import (
    UNKNOWN_TYPE_DURATION_MIN,
    CommandError,
    Weekday,
    WorkoutType,
    parse_new_command,
)


def test_valid_command():
    draft = parse_new_command("/new strength Пн,Ср,Пт 19:30")

    assert draft.workout_type == WorkoutType.STRENGTH
    assert draft.weekdays == (Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY)
    assert draft.time_of_day == time(19, 30)
    assert draft.duration_min == 45


def test_command_is_case_insensitive_and_allows_single_digit_hour():
    draft = parse_new_command("/new CARDIO пн,пт 7:05")

    assert draft.workout_type == WorkoutType.CARDIO
    assert draft.weekdays == (Weekday.MONDAY, Weekday.FRIDAY)
    assert draft.time_of_day == time(7, 5)
    assert draft.duration_min == 30


def test_bot_mention_in_command_is_ignored():
    draft = parse_new_command("/new@TrainerBot mobility сб 10:00")

    assert draft.workout_type == WorkoutType.MOBILITY
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
    with pytest.raises(CommandError, match="ЧЧ:ММ"):
        parse_new_command("/new strength Пн 19-30")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("20", time(20, 0)), ("7", time(7, 0)), ("0", time(0, 0)),
     ("23", time(23, 0)), ("20:15", time(20, 15))],
)
def test_time_accepts_hour_without_minutes(raw, expected):
    assert parse_new_command(f"/new бег Пн {raw}").time_of_day == expected


@pytest.mark.parametrize("raw", ["24", "25", "99", "19-30"])
def test_bad_hour_is_rejected(raw):
    with pytest.raises(CommandError):
        parse_new_command(f"/new бег Пн {raw}")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("40", 40), ("1ч30", 90), ("2ч", 120), ("1:30", 90), ("0:30", 30), ("0:05", 5)],
)
def test_duration_accepts_minutes_and_hours(raw, expected):
    draft = parse_new_command(f"/new бег Пн 19:30 {raw}")

    assert draft.duration_min == expected


@pytest.mark.parametrize("raw", ["1ч70", "11:00", "0:02", "1ч00м", "ч", "1:5"])
def test_bad_duration_formats_are_rejected(raw):
    with pytest.raises(CommandError):
        parse_new_command(f"/new бег Пн 19:30 {raw}")


def test_unknown_weekday_is_rejected_with_allowed_list():
    with pytest.raises(CommandError, match="Неизвестный день") as exc:
        parse_new_command("/new cardio Пн,Сббота 08:00")

    assert "пн" in str(exc.value)


def test_empty_day_in_list_is_rejected():
    with pytest.raises(CommandError, match="Пустой день"):
        parse_new_command("/new strength Пн,,Ср 19:30")


def test_free_type_is_accepted_with_default_duration():
    # R-33: тип — свободная фраза, а не слово из списка.
    draft = parse_new_command("/new бег Пн 19:30")

    assert draft.workout_type == "бег"
    assert draft.duration_min == UNKNOWN_TYPE_DURATION_MIN


def test_too_long_type_is_rejected():
    with pytest.raises(CommandError, match="Слишком длинный тип"):
        parse_new_command("/new " + "бег" * 20 + " Пн 19:30")


def test_wrong_argument_count_is_rejected():
    with pytest.raises(CommandError, match="три аргумента"):
        parse_new_command("/new strength Пн 19:30 40 лишнее")


def test_duration_argument_is_optional_and_applied():
    # R-01: четыре аргумента — длительность задана, три — по умолчанию.
    explicit = parse_new_command("/new бег Пн,Ср 19:30 40")
    default = parse_new_command("/new бег Пн,Ср 19:30")

    assert explicit.duration_min == 40
    assert default.duration_min == UNKNOWN_TYPE_DURATION_MIN


@pytest.mark.parametrize("raw", ["4", "601", "40.5", "много", "-5"])
def test_bad_duration_argument_is_rejected(raw):
    with pytest.raises(CommandError, match="Длительность"):
        parse_new_command(f"/new бег Пн 19:30 {raw}")


def test_other_command_is_rejected():
    with pytest.raises(CommandError, match="Ожидалась команда /new"):
        parse_new_command("/list")


def test_empty_text_is_rejected():
    with pytest.raises(CommandError, match="Пустая команда"):
        parse_new_command("   ")
