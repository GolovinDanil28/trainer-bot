"""Создание тренировки фразой: свободный тип (R-33) и дистанция (R-32)."""

from datetime import time as clock_time

import pytest
from sqlalchemy import text

from src.core.assistant import IntentError, parse_intent
from src.core.messages import format_schedule
from src.core.schedule import (
    PACE_MIN_PER_KM,
    UNKNOWN_TYPE_DURATION_MIN,
    CommandError,
    Weekday,
    WorkoutView,
    duration_for,
    duration_from_distance,
    make_weekly_draft,
    normalize_workout_type,
)
from src.db.session import apply_migrations, create_db_engine

# --- Дистанция → минуты (R-32) -------------------------------------------


@pytest.mark.parametrize(
    ("km", "expected"),
    [
        (7, 70),
        (5.2, 50),
        (1, 10),
        (0.2, 5),  # 2 минуты округляются вниз до шага, но не ниже минимума
        (4.9, 45),
    ],
)
def test_distance_becomes_minutes(km, expected):
    assert duration_from_distance(km) == expected


def test_pace_is_one_constant():
    # 7 км при темпе 10 мин/км — это 70 минут; если поменять константу,
    # поменяется и это ожидание.
    assert duration_from_distance(7) == 7 * PACE_MIN_PER_KM


def test_explicit_minutes_win_over_distance():
    # «7 км на 40 минут» — просьба, а не подсказка (R-32).
    assert duration_for("бег", distance_km=7, duration_min=40) == 40


def test_distance_used_when_minutes_absent():
    assert duration_for("бег", distance_km=7) == 70


def test_unknown_type_default_duration():
    assert duration_for("интервальный бег", None) == UNKNOWN_TYPE_DURATION_MIN


def test_weekly_draft_keeps_both_values():
    draft = make_weekly_draft(
        "бег", (Weekday.MONDAY,), clock_time(19, 0), distance_km=7
    )

    assert draft.distance_km == 7
    assert draft.duration_min == 70


# --- Свободный тип (R-33) -------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("бег", "бег"),
        ("  Интервальный   Бег ", "интервальный бег"),
        ("CARDIO", "cardio"),
    ],
)
def test_free_type_is_normalized(raw, expected):
    assert normalize_workout_type(raw) == expected


@pytest.mark.parametrize("raw", ["", "б", "бег" * 11, "бе*г", "бег_дня"])
def test_bad_free_type_is_rejected(raw):
    with pytest.raises(CommandError):
        normalize_workout_type(raw)


def test_intent_accepts_free_type_words():
    intent = parse_intent(
        '{"action":"create","type":"интервальный бег","days":["Ср"],'
        '"time":"19:00","distance_km":7}',
        "интервальный бег 7 км по средам в 19:00",
    )

    assert intent.type == "интервальный бег"
    assert intent.distance_km == 7


@pytest.mark.parametrize("raw_distance", [0, -1, 101, "7"])
def test_bad_distance_is_rejected(raw_distance):
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","days":["Ср"],"time":"19:00",'
            f'"distance_km":{raw_distance!r}}}',
            "бег по средам в 19:00",
        )


@pytest.mark.parametrize("raw_minutes", [4, 601, 12.5, "40"])
def test_bad_duration_is_rejected(raw_minutes):
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"бег","days":["Ср"],"time":"19:00",'
            f'"duration_min":{raw_minutes!r}}}',
            "бег по средам в 19:00",
        )


def test_type_not_mentioned_in_text_is_rejected():
    # Модель не может выдумать вид тренировки, которого нет во фразе (R-15).
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":"плавание","days":["Ср"],"time":"19:00"}',
            "пробежка по средам в 19:00",
        )


# --- Показ в /list (R-32) -------------------------------------------------


def _view(**overrides):
    values = {
        "id": 1,
        "workout_type": "бег",
        "weekdays": (Weekday.MONDAY,),
        "time_of_day": clock_time(19, 0),
        "duration_min": 70,
        "distance_km": 7.0,
    }
    values.update(overrides)
    return WorkoutView(**values)


def test_list_shows_distance_and_minutes():
    assert "7 км (≈70 мин)" in format_schedule([_view()])


def test_list_without_distance_shows_only_minutes():
    text = format_schedule([_view(distance_km=None, duration_min=45)])

    assert "45 мин" in text
    assert "км" not in text


# --- Миграция схемы (1.1) -------------------------------------------------


def test_migration_adds_columns_to_old_database(tmp_path):
    """Старая база без distance_km/date_once/time_once догоняется миграцией."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE workout ("
                "id INTEGER PRIMARY KEY, workout_type VARCHAR(16), "
                "duration_min INTEGER, is_active BOOLEAN, created_at DATETIME)"
            )
        )

    apply_migrations(engine)

    columns = _columns(engine, "workout")
    assert {"distance_km", "date_once", "time_once"} <= columns


def test_migration_is_idempotent(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE workout ("
                "id INTEGER PRIMARY KEY, workout_type VARCHAR(16), "
                "duration_min INTEGER, is_active BOOLEAN, created_at DATETIME)"
            )
        )

    apply_migrations(engine)
    apply_migrations(engine)  # второй вызов не должен падать

    assert "distance_km" in _columns(engine, "workout")


def _columns(engine, table):
    with engine.begin() as connection:
        return {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
