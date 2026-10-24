"""Тест уборщика старых напоминаний: 4.4 R-08.

Проверяет, что `sent` и `pending` старые более чем на 30 сек переводятся в `missed`,
а `answered` оставляем в покое.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import update as sq_update

from src.core.reminders import ReminderStatus
from src.db.models import Reminder


@pytest.fixture
def engine(tmp_path):
    from src.db.session import create_db_engine, init_db
    engine = create_db_engine(f"sqlite:///{tmp_path / 'trainer.db'}")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    from src.db.session import make_session_factory
    return make_session_factory(engine)


def test_cleanup_old_sent_reminders(session_factory):
    """sent старше 30 секунд → missed."""
    from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
    from src.db import repository as repo_mod

    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=45,
    )
    with session_factory() as session:
        wid = repo_mod.create_workout(session, draft).id
        moment = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)
        rid = repo_mod.claim_reminder(session, wid, moment)

    # Вручнуюставим sent_at давным (30+ сек назад) через execute(update(...).values())
    old_time = datetime(2026, 10, 26, 19, 29, 30, tzinfo=UTC)  # 30 сек назад
    with session_factory() as session:
        session.execute(
            sq_update(Reminder)
            .where(Reminder.id == rid)
            .values(status=ReminderStatus.SENT.value, sent_at=old_time)
        )
        session.commit()

    now = datetime(2026, 10, 26, 19, 31, tzinfo=UTC)  # сейчас

    with session_factory() as session:
        count = repo_mod.cleanup_old_reminders(session, now)

    # Должно быть обновлено 1 строки
    assert count == 1

    with session_factory() as session:
        r = session.get(Reminder, rid)
        assert r.status == ReminderStatus.MISSED.value
        assert r.attempts == 0


def test_cleanup_old_pending_reminders(session_factory):
    """pending старше 30 секунд → missed."""
    from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
    from src.db import repository as repo_mod

    draft = WorkoutDraft(
        workout_type=WorkoutType.CARDIO,
        weekdays=(Weekday.WEDNESDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=30,
    )
    with session_factory() as session:
        wid = repo_mod.create_workout(session, draft).id

    # Создадим pending напоминание вручную (без claim, чтобы проверить old pending)
    from src.db.models import Reminder
    with session_factory() as session:
        session.add(
            Reminder(
                workout_id=wid,
                scheduled_at=datetime(2026, 10, 26, 19, 30).replace(tzinfo=None),  # naive UTC
                status=ReminderStatus.PENDING.value,
                created_at=datetime(2026, 10, 26, 19, 29, 30, tzinfo=UTC),
            )
        )
        session.commit()

    now = datetime(2026, 10, 26, 19, 31, tzinfo=UTC)

    with session_factory() as session:
        count = repo_mod.cleanup_old_reminders(session, now)

    assert count == 1

    with session_factory() as session:
        r = session.get(Reminder, session.query(Reminder).filter_by(workout_id=wid).first().id)
        assert r.status == ReminderStatus.MISSED.value


def test_cleanup_does_not_touch_answered(session_factory):
    """answered не трогаем вообще — остаются answered."""
    from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
    from src.db import repository as repo_mod

    draft = WorkoutDraft(
        workout_type=WorkoutType.MOBILITY,
        weekdays=(Weekday.SUNDAY,),
        time_of_day=datetime(2026, 10, 26, 10, 0).time(),
        duration_min=20,
    )
    with session_factory() as session:
        wid = repo_mod.create_workout(session, draft).id

    # Создадим answered напоминание со старым sent_at
    from src.db.models import Reminder
    old_time = datetime(2026, 10, 26, 10, 59, 30, tzinfo=UTC)  # 30+ сек назад
    with session_factory() as session:
        # Сначала claim и отправь как answered
        moment = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
        rid = repo_mod.claim_reminder(session, wid, moment)
        # Используем ту же самую паттерн execute(update(...).values())
        session.execute(
            sq_update(Reminder).where(Reminder.id == rid).values(
                status=ReminderStatus.ANSWERED.value, sent_at=old_time
            )
        )
        session.commit()

    now = datetime(2026, 10, 26, 10, 31, tzinfo=UTC)  # 30+ сек спустя

    with session_factory() as session:
        count = repo_mod.cleanup_old_reminders(session, now)

    # answered не должно за impacted
    assert count == 0

    with session_factory() as session:
        r = session.get(Reminder, rid)
        assert r.status == ReminderStatus.ANSWERED.value