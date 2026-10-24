"""Тесты репозитория: что реально оказалось в базе."""

from datetime import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository
from src.db.models import Schedule, Workout


@pytest.fixture
def draft() -> WorkoutDraft:
    return WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY),
        time_of_day=time(19, 30),
        duration_min=45,
    )


def test_create_workout_writes_header_and_schedule(engine, session_factory, draft):
    with session_factory() as session:
        view = repository.create_workout(session, draft)

        assert view.id == 1
        assert view.workout_type is WorkoutType.STRENGTH
        assert view.weekdays == draft.weekdays
        assert view.time_of_day == time(19, 30)

        workouts = session.scalar(select(func.count()).select_from(Workout))
        rows = session.scalars(select(Schedule)).all()

    assert workouts == 1
    assert sorted(row.weekday for row in rows) == [0, 2, 4]
    assert {row.time_of_day for row in rows} == {"19:30"}


def test_duplicate_schedule_row_is_rejected_by_database(session_factory, draft):
    with session_factory() as session:
        repository.create_workout(session, draft)
        # Повторение того же дня и времени для той же тренировки ломает
        # уникальный ключ — защита на уровне данных, а не на уровне кода.
        session.add(Schedule(workout_id=1, weekday=0, time_of_day="19:30"))

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_list_active_returns_only_active_and_is_sorted(session_factory, draft):
    with session_factory() as session:
        first = repository.create_workout(session, draft)
        second = repository.create_workout(session, draft)
        repository.deactivate(session, first.id)

        views = repository.list_active(session)

    assert [view.id for view in views] == [second.id]


def test_deactivate_switches_off_workout_and_schedule(session_factory, draft):
    with session_factory() as session:
        created = repository.create_workout(session, draft)

        assert repository.deactivate(session, created.id) is True
        # Повторное выключение — уже нечего менять.
        assert repository.deactivate(session, created.id) is False

        workout = session.get(Workout, created.id)
        rows = session.scalars(select(Schedule)).all()

    assert workout is not None
    assert workout.is_active is False
    assert all(row.is_active is False for row in rows)
    # История сохраняется: строки расписания не удалены.
    assert len(rows) == 3


def test_unknown_workout_is_not_deactivated(session_factory):
    with session_factory() as session:
        assert repository.deactivate(session, 99) is False
