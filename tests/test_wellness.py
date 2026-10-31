"""Самочувствие: домен и хранение.

Домен проверяется строками и структурами (как `core/schedule`), хранение — на
настоящей SQLite в `tmp_path`: уникальность дня и upsert должны держаться базой,
а не проверкой в коде.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from src.core.reminders import CallbackError
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.core.wellness import (
    NOTE_LIMIT,
    WellnessCommandError,
    WellnessEntry,
    WellnessScore,
    parse_wellness_callback,
    parse_wellness_command,
    wellness_callback,
)
from src.db import repository
from src.db.models import Schedule, Wellness

DAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


# --- домен: команда -------------------------------------------------------


def test_parse_command_with_note():
    score, note = parse_wellness_command("/wellness 2 не выспался")

    assert score == WellnessScore(2)
    assert note == "не выспался"


def test_parse_command_without_note():
    score, note = parse_wellness_command("/wellness 5")

    assert score == WellnessScore(5)
    assert note is None


def test_parse_command_collapses_spaces_in_note():
    _, note = parse_wellness_command("/wellness 4   много    работы ")

    assert note == "много работы"


def test_parse_command_truncates_long_note():
    _, note = parse_wellness_command("/wellness 3 " + "а" * (NOTE_LIMIT + 50))

    assert note is not None
    assert len(note) == NOTE_LIMIT


def test_parse_command_ignores_bot_mention():
    score, _ = parse_wellness_command("/wellness@TrainerBot 3")

    assert score == WellnessScore(3)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "/wellness",
        "/wellness 0",
        "/wellness 6",
        "/wellness 7",
        "/wellness abc",
        "/wellness 3.5",
        "/new strength Пн 10:00",
    ],
    ids=[
        "пусто",
        "без-аргумента",
        "ноль",
        "шесть",
        "семь",
        "не-число",
        "дробное",
        "чужая-команда",
    ],
)
def test_parse_command_rejects_invalid_input(text):
    with pytest.raises(WellnessCommandError):
        parse_wellness_command(text)


def test_score_rejects_out_of_range():
    with pytest.raises(ValueError):
        WellnessScore(0)
    with pytest.raises(ValueError):
        WellnessScore(6)


# --- домен: кнопка --------------------------------------------------------


def test_callback_roundtrip():
    data = wellness_callback(DAY, 3)

    assert data == "wl:2026-10-27:3"
    assert parse_wellness_callback(data) == (DAY, 3)


@pytest.mark.parametrize(
    "data",
    [
        "",
        "wl:2026-10-27",
        "wl:2026-10-27:9",
        "wl:2026-10-27:abc",
        "wl:сегодня:3",
        "ci:1:done",
    ],
    ids=["пусто", "без-оценки", "оценка-9", "оценка-словом", "день-словом", "чужая-кнопка"],
)
def test_callback_rejects_garbage(data):
    with pytest.raises(CallbackError):
        parse_wellness_callback(data)


def test_wellness_entry_knows_if_answered():
    assert WellnessEntry(DAY, 3, None).is_answered is True
    assert WellnessEntry(DAY, None, None).is_answered is False


# --- хранение -------------------------------------------------------------


def test_row_without_score_is_saved(session_factory):
    # Состояние «вопрос отправлен, ответа нет» обязано существовать: на нём
    # держится идемпотентность вечернего вопроса (R-19).
    with session_factory() as session:
        session.add(Wellness(user_id=1, day=DAY.isoformat()))
        session.commit()

        row = session.scalar(select(Wellness))

    assert row is not None
    assert row.score is None


def test_day_is_unique_per_user(session_factory):
    """R-19/R-42: уникальность — пара (пользователь, день), не день сам по себе."""
    from sqlalchemy.exc import IntegrityError

    with session_factory() as session:
        session.add(Wellness(user_id=1, day=DAY.isoformat(), score=4))
        session.commit()

    # Та же пара — ограничение сработает.
    with session_factory() as session, pytest.raises(IntegrityError):
        session.add(Wellness(user_id=1, day=DAY.isoformat(), score=2))
        session.commit()

    # Другой пользователь за тот же день — можно.
    from src.db.models import User

    with session_factory() as session:
        session.add(
            User(id=2, telegram_id=555, tz="UTC", wellness_time="21:00",
                 week_review_time="20:00")
        )
        session.add(Wellness(user_id=2, day=DAY.isoformat(), score=2))
        session.commit()

def test_claim_prompt_succeeds_once(session_factory):
    with session_factory() as session:
        assert repository.claim_wellness_prompt(session, DAY, NOW) is True

    with session_factory() as session:
        assert repository.claim_wellness_prompt(session, DAY, NOW) is False


def test_claim_prompt_refused_when_already_rated(session_factory):
    with session_factory() as session:
        repository.save_wellness(session, DAY, 4, None, NOW)

    with session_factory() as session:
        assert repository.claim_wellness_prompt(session, DAY, NOW) is False


def test_save_wellness_creates_entry(session_factory):
    with session_factory() as session:
        entry = repository.save_wellness(session, DAY, 3, "не выспался", NOW)

    assert entry == WellnessEntry(DAY, 3, "не выспался")


def test_save_wellness_replaces_score_and_keeps_note(session_factory):
    with session_factory() as session:
        repository.save_wellness(session, DAY, 4, "нормально", NOW)

    with session_factory() as session:
        entry = repository.save_wellness(session, DAY, 2, None, NOW)

    assert entry == WellnessEntry(DAY, 2, "нормально")


def test_save_wellness_sets_answered_at(session_factory):
    with session_factory() as session:
        repository.save_wellness(session, DAY, 5, None, NOW)

        row = session.scalar(select(Wellness))

    assert row is not None
    assert row.answered_at is not None


def test_save_wellness_keeps_prompt_time(session_factory):
    # Оценка после вопроса не должна стирать факт отправки: иначе следующий тик
    # посчитает день свободным и спросит второй раз.
    with session_factory() as session:
        repository.claim_wellness_prompt(session, DAY, NOW)
        repository.save_wellness(session, DAY, 3, "ок", NOW)

        row = session.scalar(select(Wellness))

    assert row is not None
    assert row.prompted_at is not None
    assert row.score == 3


def test_wellness_for_day_missing(session_factory):
    with session_factory() as session:
        assert repository.wellness_for_day(session, DAY) is None


def test_release_prompt_frees_only_unanswered_day(session_factory):
    with session_factory() as session:
        assert repository.claim_wellness_prompt(session, DAY, NOW) is True

    with session_factory() as session:
        assert repository.release_wellness_prompt(session, DAY) is True

    with session_factory() as session:
        assert repository.wellness_for_day(session, DAY) is None

    with session_factory() as session:
        repository.save_wellness(session, DAY, 4, None, NOW)
        assert repository.release_wellness_prompt(session, DAY) is False
        assert repository.wellness_for_day(session, DAY) == WellnessEntry(DAY, 4, None)


def make_workout(session_factory, weekday: Weekday, hour: int) -> int:
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(weekday,),
        time_of_day=datetime(2026, 10, 26, hour, 0).time(),
        duration_min=45,
    )
    with session_factory() as session:
        return repository.create_workout(session, draft).id


def test_workouts_for_weekday_filters_inactive_workout(session_factory):
    monday_id = make_workout(session_factory, Weekday.MONDAY, 19)
    make_workout(session_factory, Weekday.MONDAY, 8)
    make_workout(session_factory, Weekday.TUESDAY, 19)
    with session_factory() as session:
        repository.deactivate(session, monday_id)

    with session_factory() as session:
        workouts = repository.workouts_for_weekday(session, Weekday.MONDAY)

    assert [workout.duration_min for workout in workouts] == [45]
    assert len(workouts) == 1


def test_workouts_for_weekday_filters_inactive_schedule_row(session_factory):
    workout_id = make_workout(session_factory, Weekday.MONDAY, 19)
    with session_factory() as session:
        session.execute(
            Schedule.__table__.update()
            .where(Schedule.workout_id == workout_id)
            .values(is_active=False)
        )
        session.commit()

    with session_factory() as session:
        assert repository.workouts_for_weekday(session, Weekday.MONDAY) == []


def test_workouts_for_weekday_keeps_order_and_uniqueness(session_factory):
    make_workout(session_factory, Weekday.MONDAY, 19)
    make_workout(session_factory, Weekday.MONDAY, 8)

    with session_factory() as session:
        workouts = repository.workouts_for_weekday(session, Weekday.MONDAY)

    assert [workout.id for workout in workouts] == sorted(
        workout.id for workout in workouts
    )
    assert len({workout.id for workout in workouts}) == len(workouts)
