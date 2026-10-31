"""Несколько пользователей: миграция, изоляция данных, доступ (R-41, R-42)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from src.bot.access import OWNER_KEY, SESSION_FACTORY_KEY, resolve_user_id
from src.bot.service import WorkoutService
from src.core.plan import build_plan
from src.core.reminders import CheckinAction
from src.core.schedule import (
    Weekday,
    WorkoutView,
    make_weekly_draft,
)
from src.core.wellness import WellnessScore
from src.db import repository
from src.db.context import DEFAULT_USER_ID, acting_as
from src.db.models import User, Workout
from src.db.session import create_db_engine, init_db, make_session_factory
from tests.fakes import make_context, make_update

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DAY = date(2026, 11, 2)


def _register(session_factory, user_id: int, telegram_id: int) -> None:
    with session_factory() as session:
        session.add(
            User(
                id=user_id,
                telegram_id=telegram_id,
                tz="UTC",
                wellness_time="21:00",
                week_review_time="20:00",
            )
        )
        session.commit()


def _create(session_factory, user_id: int, name: str = "бег") -> int:
    with session_factory() as session, acting_as(user_id):
        view = repository.create_workout(
            session, make_weekly_draft(name, (Weekday.MONDAY,), time(19, 0))
        )
    return view.id


# --- Изоляция данных ------------------------------------------------------


def test_lists_are_isolated(session_factory):
    _register(session_factory, 2, 555)
    first = _create(session_factory, 1, "бег")
    second = _create(session_factory, 2, "силовая")

    with session_factory() as session:
        with acting_as(1):
            own = repository.list_active(session)
        with acting_as(2):
            own_two = repository.list_active(session)

    assert [view.id for view in own] == [first]
    assert [view.id for view in own_two] == [second]


def test_cannot_deactivate_foreign_workout(session_factory):
    _register(session_factory, 2, 555)
    owned_by_first = _create(session_factory, 1)

    with session_factory() as session, acting_as(2):
        assert repository.deactivate(session, owned_by_first) is False

    with session_factory() as session, acting_as(1):
        assert [view.id for view in repository.list_active(session)] == [owned_by_first]


def test_same_day_wellness_for_two_users(session_factory):
    _register(session_factory, 2, 555)

    with session_factory() as session, acting_as(1):
        repository.save_wellness(session, DAY, 4, None, NOW)
    with session_factory() as session, acting_as(2):
        repository.save_wellness(session, DAY, 2, "устал", NOW)

    with session_factory() as session, acting_as(1):
        assert repository.wellness_for_day(session, DAY).score == 4
    with session_factory() as session, acting_as(2):
        entry = repository.wellness_for_day(session, DAY)
    assert (entry.score, entry.note) == (2, "устал")


def test_plan_snapshots_are_isolated(session_factory):
    _register(session_factory, 2, 555)

    with session_factory() as session, acting_as(1):
        workout = repository.create_workout(
            session, make_weekly_draft("бег", (Weekday.TUESDAY,), time(8, 0))
        )
        view = WorkoutView(
            id=workout.id,
            workout_type="бег",
            weekdays=(Weekday.TUESDAY,),
            time_of_day=time(8, 0),
            duration_min=30,
        )
        repository.save_plan_snapshot(session, DAY, build_plan(WellnessScore(4), [view]))

    with session_factory() as session, acting_as(2):
        repository.save_plan_snapshot(session, DAY, build_plan(WellnessScore(1), []))

    with session_factory() as session:
        with acting_as(1):
            first = repository.week_snapshots(session, DAY, DAY)
        with acting_as(2):
            second = repository.week_snapshots(session, DAY, DAY)

    assert len(first) == 1 and first[0].items
    assert len(second) == 1 and second[0].is_rest


def test_foreign_reminder_cannot_be_claimed_or_marked(session_factory):
    _register(session_factory, 2, 555)
    workout_id = _create(session_factory, 1)
    moment = datetime(2026, 11, 2, 19, 0, tzinfo=UTC)

    with session_factory() as session, acting_as(2):
        assert repository.claim_reminder(session, workout_id, moment) is None

    with session_factory() as session, acting_as(1):
        reminder_id = repository.claim_reminder(session, workout_id, moment)
    assert reminder_id is not None

    with session_factory() as session, acting_as(2):
        assert repository.record_checkin(session, reminder_id, CheckinAction.DONE) is None


def test_service_shows_only_current_user(session_factory):
    _register(session_factory, 2, 555)
    _create(session_factory, 1, "бег")
    _create(session_factory, 2, "силовая")
    service = WorkoutService(session_factory, ZoneInfo("UTC"))

    with acting_as(2):
        text = service.show_schedule()

    assert "силовая" in text
    assert "бег" not in text


# --- Доступ ---------------------------------------------------------------


def _context(session_factory, owner_id: int = 42):
    return make_context(
        {SESSION_FACTORY_KEY: session_factory, OWNER_KEY: owner_id}
    )


def test_unknown_user_is_not_registered(session_factory):
    update = make_update("привет", user_id=999)

    assert resolve_user_id(update, _context(session_factory)) is None


def test_registered_user_resolves_to_id(session_factory):
    _register(session_factory, 2, 555)
    update = make_update("привет", user_id=555)

    assert resolve_user_id(update, _context(session_factory)) == 2


def test_registered_owner_resolves_to_their_row(session_factory):
    # В базе владелец — такая же строка, как остальные (R-41).
    _register(session_factory, 5, 42)
    update = make_update("привет", user_id=42)

    assert resolve_user_id(update, _context(session_factory)) == 5


def test_owner_fallback_without_session_factory(session_factory):
    # Единственное исключение: тесты хендлеров без фабрики и первый запуск.
    from tests.fakes import make_context

    update = make_update("привет", user_id=42)

    assert resolve_user_id(update, make_context({OWNER_KEY: 42})) == DEFAULT_USER_ID


# --- Миграция старой базы -------------------------------------------------


LEGACY_SCHEMA = """
CREATE TABLE workout (
    id INTEGER PRIMARY KEY,
    workout_type VARCHAR(32) NOT NULL,
    duration_min INTEGER NOT NULL,
    distance_km FLOAT,
    date_once DATE,
    time_once VARCHAR(5),
    is_active BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE schedule (
    id INTEGER PRIMARY KEY,
    workout_id INTEGER NOT NULL REFERENCES workout(id),
    weekday INTEGER NOT NULL,
    time_of_day VARCHAR(5) NOT NULL,
    is_active BOOLEAN NOT NULL,
    UNIQUE (workout_id, weekday, time_of_day)
);
CREATE TABLE reminder (
    id INTEGER PRIMARY KEY,
    workout_id INTEGER NOT NULL REFERENCES workout(id),
    scheduled_at DATETIME NOT NULL,
    status VARCHAR(10) NOT NULL,
    attempts INTEGER NOT NULL,
    sent_at DATETIME,
    created_at DATETIME NOT NULL,
    UNIQUE (workout_id, scheduled_at)
);
CREATE TABLE checkin (
    id INTEGER PRIMARY KEY,
    reminder_id INTEGER NOT NULL UNIQUE REFERENCES reminder(id),
    status VARCHAR(10) NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE wellness (
    id INTEGER PRIMARY KEY,
    day VARCHAR(10) NOT NULL UNIQUE,
    score INTEGER,
    note VARCHAR(500),
    prompted_at DATETIME,
    answered_at DATETIME,
    created_at DATETIME NOT NULL
);
CREATE TABLE plan_snapshot (
    id INTEGER PRIMARY KEY,
    plan_date DATE NOT NULL UNIQUE,
    score INTEGER NOT NULL,
    is_rest BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE plan_item (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES plan_snapshot(id),
    workout_type VARCHAR(32) NOT NULL,
    time_of_day VARCHAR(5) NOT NULL,
    duration_min INTEGER NOT NULL,
    workout_id INTEGER
);
CREATE TABLE weekly_report (
    id INTEGER PRIMARY KEY,
    week_start DATE NOT NULL UNIQUE,
    claimed_at DATETIME,
    sent_at DATETIME
);
"""

LEGACY_ROWS = (
    "INSERT INTO workout (id, workout_type, duration_min, distance_km, is_active, "
    "created_at) VALUES (7, 'бег', 70, 7, 1, '2026-10-01 10:00:00')",
    "INSERT INTO schedule (id, workout_id, weekday, time_of_day, is_active) "
    "VALUES (1, 7, 1, '19:00', 1)",
    "INSERT INTO reminder (id, workout_id, scheduled_at, status, attempts, created_at) "
    "VALUES (3, 7, '2026-10-01 19:00:00', 'answered', 1, '2026-10-01 18:59:00')",
    "INSERT INTO checkin (id, reminder_id, status, created_at) "
    "VALUES (2, 3, 'done', '2026-10-01 19:05:00')",
    "INSERT INTO wellness (id, day, score, note, created_at) "
    "VALUES (4, '2026-10-01', 4, 'бодр', '2026-10-01 21:00:00')",
    "INSERT INTO plan_snapshot (id, plan_date, score, is_rest, created_at) "
    "VALUES (5, '2026-10-02', 3, 0, '2026-10-01 21:00:00')",
    "INSERT INTO plan_item (id, snapshot_id, workout_type, time_of_day, "
    "duration_min, workout_id) VALUES (6, 5, 'бег', '19:00', 45, 7)",
    # claimed_at NULL — так выглядят строки, созданные до появления колонки.
    "INSERT INTO weekly_report (id, week_start, claimed_at, sent_at) "
    "VALUES (8, '2026-09-28', NULL, '2026-10-04 20:00:00')",
)

_TABLES = (
    "workout", "schedule", "reminder", "checkin",
    "wellness", "plan_snapshot", "plan_item", "weekly_report",
)


def _legacy_database(tmp_path, name: str = "legacy.db"):
    engine = create_db_engine(f"sqlite:///{tmp_path / name}")
    with engine.begin() as connection:
        for statement in LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        for statement in LEGACY_ROWS:
            connection.exec_driver_sql(statement)
    return engine


def _counts(engine):
    with engine.connect() as connection:
        return {
            table: connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar()
            for table in _TABLES
        }


def test_legacy_database_migrates_and_keeps_data(tmp_path):
    engine = _legacy_database(tmp_path)
    before = _counts(engine)

    init_db(engine, owner_telegram_id=777, tz="Asia/Yekaterinburg")

    assert _counts(engine) == before
    factory = make_session_factory(engine)
    with factory() as session:
        owner = session.scalar(select(User).where(User.telegram_id == 777))
        workout = session.scalar(select(Workout).where(Workout.id == 7))
        report = session.execute(
            text("SELECT claimed_at FROM weekly_report WHERE id = 8")
        ).scalar()
        assert owner is not None and owner.tz == "Asia/Yekaterinburg"
        assert workout is not None and workout.user_id == owner.id
        # NULL claimed_at не должен ронять миграцию и не должен остаться NULL.
        assert report is not None


def test_legacy_database_without_claimed_column_migrates(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'older.db'}")
    with engine.begin() as connection:
        for statement in LEGACY_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        # Старая-старая схема: колонки claimed_at ещё нет.
        connection.exec_driver_sql("DROP TABLE weekly_report")
        connection.exec_driver_sql(
            "CREATE TABLE weekly_report (id INTEGER PRIMARY KEY, "
            "week_start DATE NOT NULL UNIQUE, sent_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO weekly_report (id, week_start, sent_at) "
            "VALUES (8, '2026-09-28', '2026-10-04 20:00:00')"
        )

    init_db(engine, owner_telegram_id=777)

    factory = make_session_factory(engine)
    with factory() as session:
        report = session.execute(
            text("SELECT claimed_at FROM weekly_report WHERE id = 8")
        ).scalar()
    assert report is not None


def test_failed_migration_restores_original_database(tmp_path, monkeypatch):
    engine = _legacy_database(tmp_path)
    before = _counts(engine)
    engine.dispose()

    # Сбой после DROP: владелец «не создаётся». Без копии данные были бы
    # потеряны, а второй запуск молча поднял бы пустого бота.
    import src.db.session as session_module

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("сбой посреди миграции")

    monkeypatch.setattr(session_module, "_ensure_owner_row", boom)
    with pytest.raises(RuntimeError):
        init_db(engine, owner_telegram_id=777)

    # База должна остаться прежней: файл восстановлен из копии.
    restored = create_db_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    assert _counts(restored) == before
    with restored.connect() as connection:
        columns = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(workout)")]
    assert "user_id" not in columns


def test_migration_is_idempotent(tmp_path):
    engine = _legacy_database(tmp_path)
    init_db(engine, owner_telegram_id=777)
    init_db(engine, owner_telegram_id=777)

    factory = make_session_factory(engine)
    with factory() as session:
        assert len(list(session.scalars(select(User)))) == 1
        assert len(list(session.scalars(select(Workout)))) == 1


def test_wellness_prompt_claim_is_per_user(session_factory):
    _register(session_factory, 2, 555)
    now = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)

    with session_factory() as session, acting_as(1):
        assert repository.claim_wellness_prompt(session, DAY, now) is True
    with session_factory() as session, acting_as(2):
        assert repository.claim_wellness_prompt(session, DAY, now) is True
    with session_factory() as session, acting_as(1):
        # Повторный тик того же пользователя права не получает.
        assert repository.claim_wellness_prompt(session, DAY, now) is False


def test_history_and_week_reads_are_isolated(session_factory):

    _register(session_factory, 2, 555)
    first = _create(session_factory, 1)
    second = _create(session_factory, 2)
    moment = datetime(2026, 11, 2, 19, 0, tzinfo=UTC)

    with session_factory() as session, acting_as(1):
        reminder = repository.claim_reminder(session, first, moment)
        session.execute(
            text("UPDATE reminder SET status = 'answered' WHERE id = :id"),
            {"id": reminder},
        )
        session.commit()
    with session_factory() as session, acting_as(2):
        repository.claim_reminder(session, second, moment)

    with session_factory() as session, acting_as(1):
        own = repository.recent_history(session)
        own_reminders = repository.week_reminders(
            session, moment - __import__("datetime").timedelta(hours=1),
            moment + __import__("datetime").timedelta(hours=1),
        )
    with session_factory() as session, acting_as(2):
        other = repository.recent_history(session)
        other_reminders = repository.week_reminders(
            session, moment - __import__("datetime").timedelta(hours=1),
            moment + __import__("datetime").timedelta(hours=1),
        )

    assert [entry.workout_type for entry in own] == ["бег"]
    assert other == []
    assert {item.workout_id for item in own_reminders} == {first}
    assert {item.workout_id for item in other_reminders} == {second}


def test_schedule_snapshot_for_model_is_own_only(session_factory):
    _register(session_factory, 2, 555)
    _create(session_factory, 1, "бег")
    _create(session_factory, 2, "силовая")
    service = WorkoutService(session_factory, ZoneInfo("UTC"))

    with acting_as(2):
        snapshot = service.schedule_snapshot()

    assert "силовая" in snapshot
    assert "бег" not in snapshot


def test_replacing_one_snapshot_keeps_other_user(session_factory):
    _register(session_factory, 2, 555)
    rest = build_plan(WellnessScore(1), [])
    active = build_plan(WellnessScore(4), [])

    with session_factory() as session, acting_as(1):
        repository.save_plan_snapshot(session, DAY, active)
    with session_factory() as session, acting_as(2):
        repository.save_plan_snapshot(session, DAY, rest)
    with session_factory() as session, acting_as(1):
        repository.save_plan_snapshot(session, DAY, active)

    with session_factory() as session, acting_as(2):
        entry = repository.week_snapshots(session, DAY, DAY)

    assert len(entry) == 1 and entry[0].is_rest
