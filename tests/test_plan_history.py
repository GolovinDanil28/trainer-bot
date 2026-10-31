"""Снимок плана (R-36): пишется при показе, один на дату, переживает отключение."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.schedule import Weekday, make_weekly_draft
from src.db import repository
from src.db.models import PlanSnapshot, PlanSnapshotItem, Workout

ZONE = ZoneInfo("UTC")
TODAY = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)  # понедельник
TOMORROW = date(2026, 11, 3)


@pytest.fixture
def wellness(session_factory) -> WellnessService:
    return WellnessService(session_factory, ZONE, clock=lambda: TODAY)


def _add_workout(session_factory, weekdays=(Weekday.TUESDAY,), at=time(8, 0)):
    with session_factory() as session:
        repository.create_workout(
            session, make_weekly_draft("бег", tuple(weekdays), at, distance_km=7)
        )


def _answer_wellness(session_factory, score: int = 4, note: str | None = None) -> None:
    with session_factory() as session:
        repository.save_wellness(session, TODAY.date(), score, note, TODAY)


def _snapshots(session_factory) -> list[PlanSnapshot]:
    with session_factory() as session:
        return list(session.scalars(select(PlanSnapshot).order_by(PlanSnapshot.id)))


def _items(session_factory) -> list[PlanSnapshotItem]:
    with session_factory() as session:
        return list(session.scalars(select(PlanSnapshotItem).order_by(PlanSnapshotItem.id)))


def test_plan_show_writes_snapshot(session_factory, wellness):
    _add_workout(session_factory)
    _answer_wellness(session_factory)

    wellness.show_plan(TODAY)

    snapshots = _snapshots(session_factory)
    assert len(snapshots) == 1
    assert snapshots[0].plan_date == TOMORROW
    assert snapshots[0].is_rest is False
    items = _items(session_factory)
    assert len(items) == 1
    assert items[0].workout_type == "бег"
    assert items[0].time_of_day == "08:00"


def test_second_show_replaces_snapshot(session_factory, wellness):
    _add_workout(session_factory)
    _answer_wellness(session_factory)
    wellness.show_plan(TODAY)

    # Расписание поменялось между показами: снимок обязан стать новым, но один.
    _add_workout(session_factory, at=time(19, 0))
    wellness.show_plan(TODAY)

    assert len(_snapshots(session_factory)) == 1
    items = _items(session_factory)
    assert len(items) == 2
    assert {item.time_of_day for item in items} == {"08:00", "19:00"}


def test_rest_plan_snapshot_has_no_items(session_factory, wellness):
    _add_workout(session_factory)
    today = TODAY
    with session_factory() as session:
        repository.save_wellness(session, today.date(), 1, "разбит", TODAY)

    wellness.show_plan(TODAY)

    snapshots = _snapshots(session_factory)
    assert len(snapshots) == 1
    assert snapshots[0].is_rest is True
    assert snapshots[0].score == 1
    assert _items(session_factory) == []


def test_no_score_means_no_snapshot(session_factory, wellness):
    wellness.show_plan(TODAY)

    assert _snapshots(session_factory) == []


def test_plan_does_not_change_schedule(session_factory, wellness):
    # R-24: снимок — журнал показа, а не изменение расписания.
    _add_workout(session_factory)
    service = WorkoutService(session_factory, ZONE, clock=lambda: TODAY)

    before = service.show_schedule()
    wellness.show_plan(TODAY)
    after = service.show_schedule()

    assert before == after
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 1
