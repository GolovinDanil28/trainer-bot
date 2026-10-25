"""Тест доставки напоминаний: три попытки и не止不动 цикл.

Проверяет R-09: транспорт может падать, но обработка всех напоминаний
не останавливается, зависшие переводятся в missed.
"""

from datetime import UTC, datetime

import pytest

from src.core.reminders import ReminderStatus
from src.db.models import Reminder
from src.scheduler.delivery import MAX_ATTEMPTS, ReminderDelivery
from tests.fakes import FakeSender


@pytest.fixture
def now():
    return datetime(2026, 10, 26, 19, 30, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    from src.db.session import create_db_engine, init_db, make_session_factory
    engine = create_db_engine(f"sqlite:///{tmp_path / 'trainer.db'}")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def service(session_factory):
    from zoneinfo import ZoneInfo

    from src.bot.service import WorkoutService
    return WorkoutService(session_factory, ZoneInfo("UTC"))


async def test_three_attempts_then_missed_and_continue(
    session_factory, service, now
):
    # Создадим две тренировки и занём их моменты.
    from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
    from src.db import repository

    draft1 = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=45,
    )
    with session_factory() as session:
        wid1 = repository.create_workout(session, draft1).id
        draft2 = WorkoutDraft(
            workout_type=WorkoutType.CARDIO,
            weekdays=(Weekday.WEDNESDAY,),
            time_of_day=datetime(2026, 10, 26, 19, 30).time(),
            duration_min=30,
        )
        wid2 = repository.create_workout(session, draft2).id

    # Заняем моменты напоминаний (claim) — первый шаг из 4.1
    from src.core.reminders import ReminderStatus
    moment = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)
    with session_factory() as session:
        rid1 = repository.claim_reminder(session, wid1, moment)
        rid2 = repository.claim_reminder(session, wid2, moment)

    # Транспорт, который всегда падает (fail_for = оба id)
    transport = FakeSender(failures=0, fail_for={rid1, rid2})

    # Запускаем доставку
    delivery = ReminderDelivery(session_factory, transport, max_attempts=MAX_ATTEMPTS)
    report = await delivery.deliver([rid1, rid2], now)

    # Оба напоминания должны завершиться как missed (3 попытки каждый)
    assert report.missed == [rid1, rid2], "Expected both missed"
    # У каждого должно быть 3 попытки
    assert transport.attempts_by_reminder[rid1] == 3
    assert transport.attempts_by_reminder[rid2] == 3

    # Проверить статусы в базе
    with session_factory() as session:
        r1 = session.get(Reminder, rid1)
        r2 = session.get(Reminder, rid2)
        assert r1.status == ReminderStatus.MISSED.value
        assert r2.status == ReminderStatus.MISSED.value
        assert r1.attempts == 3
        assert r2.attempts == 3


async def test_mixed_success_and_missed(
    session_factory, service, now
):
    """Один succeeds, один всегда падает, цикл продолжает второй."""
    from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
    from src.db import repository

    draft1 = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=datetime(2026, 10, 26, 19, 30).time(),
        duration_min=45,
    )
    with session_factory() as session:
        wid1 = repository.create_workout(session, draft1).id
        draft2 = WorkoutDraft(
            workout_type=WorkoutType.CARDIO,
            weekdays=(Weekday.WEDNESDAY,),
            time_of_day=datetime(2026, 10, 26, 19, 30).time(),
            duration_min=30,
        )
        wid2 = repository.create_workout(session, draft2).id

    moment = datetime(2026, 10, 26, 19, 30, tzinfo=UTC)
    with session_factory() as session:
        rid1 = repository.claim_reminder(session, wid1, moment)
        rid2 = repository.claim_reminder(session, wid2, moment)

    # Транспорт: id=rid2 всегда падает, id=rid1 всегда succeeds
    transport = FakeSender(failures=0, fail_for={rid2})

    delivery = ReminderDelivery(session_factory, transport, max_attempts=MAX_ATTEMPTS)
    report = await delivery.deliver([rid1, rid2], now)

    assert rid1 in report.sent, f"rid1 should be sent, report={report}"
    assert rid2 in report.missed, f"rid2 should be missed, report={report}"
    # rid1 taken on first attempt, rid2 took 3 attempts before gave up
    assert transport.attempts_by_reminder[rid1] == 1
    assert transport.attempts_by_reminder[rid2] == 3

    # Проверить базу
    with session_factory() as session:
        r1 = session.get(Reminder, rid1)
        r2 = session.get(Reminder, rid2)
        assert r1.status == ReminderStatus.SENT.value
        assert r2.status == ReminderStatus.MISSED.value
        assert r1.attempts == 1
        assert r2.attempts == 3