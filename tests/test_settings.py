"""Личные настройки: разбор `/settings`, сохранение, зона в запросах (R-45)."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.bot.access import (
    OWNER_KEY,
    REFUSAL_TEXT,
    REGISTRATION_KEY,
    SESSION_FACTORY_KEY,
)
from src.bot.handlers import cmd_settings
from src.bot.service import WorkoutService
from src.bot.user_service import UserService
from src.bot.wellness_service import WellnessService
from src.core.users import Registration, UserSettingsError, parse_settings_command
from src.db import repository
from src.db.context import acting_as
from src.db.models import User
from tests.fakes import make_context, make_update

NOW = datetime(2026, 11, 4, 23, 30, tzinfo=UTC)
TZ_UFA = "Asia/Yekaterinburg"  # UTC+5, локально уже 05.11


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["tz", "Asia/Yekaterinburg"], {"tz": "Asia/Yekaterinburg"}),
        (["wellness", "9:05"], {"wellness_time": "09:05"}),
        (["week", "20:00"], {"week_review_time": "20:00"}),
    ],
)
def test_parse_settings(args, expected):
    update = parse_settings_command(args)

    assert update is not None
    for key, value in expected.items():
        assert getattr(update, key) == value


def test_settings_without_args_means_show():
    assert parse_settings_command([]) is None


@pytest.mark.parametrize(
    "args",
    [
        ["tz", "Марс/Олимп"],
        ["wellness", "25:00"],
        ["wellness", "21"],
        ["week", "20:60"],
        ["что-то"],
        ["tz"],
    ],
)
def test_bad_settings_are_rejected(args):
    with pytest.raises(UserSettingsError):
        parse_settings_command(args)


# --- Сервис и команда ------------------------------------------------------


def _service(session_factory):
    return UserService(
        session_factory,
        Registration("code", TZ_UFA, "21:00", "20:00"),
    )


def test_update_persists_and_normalizes(session_factory):
    service = _service(session_factory)

    text = service.settings(1, ["wellness", "9:05"])

    with session_factory() as session:
        user = session.scalar(select(User).where(User.id == 1))
    assert user is not None and user.wellness_time == "09:05"
    assert "09:05" in text


def test_show_settings(session_factory):
    text = _service(session_factory).settings(1, [])

    assert "таймзона" in text
    assert "21:00" in text and "20:00" in text


def _context(session_factory):
    service = _service(session_factory)
    return make_context(
        {
            SESSION_FACTORY_KEY: session_factory,
            REGISTRATION_KEY: Registration("", TZ_UFA, "21:00", "20:00"),
            "user_service": service,
            OWNER_KEY: 42,
        },
        args=["wellness", "22:00"],
    )


async def test_settings_command_saves(session_factory):
    with session_factory() as session:
        session.add(
            User(id=5, telegram_id=42, tz="UTC", wellness_time="21:00",
                 week_review_time="20:00")
        )
        session.commit()

    update = make_update("/settings wellness 22:00", user_id=42)
    await cmd_settings(update, _context(session_factory))

    with session_factory() as session:
        user = session.scalar(select(User).where(User.id == 5))
    assert user is not None and user.wellness_time == "22:00"
    assert update.message.parse_modes == ["HTML"]


async def test_settings_stranger_refused(session_factory):
    update = make_update("/settings", user_id=999)

    await cmd_settings(update, _context(session_factory))

    assert update.message.sent == [REFUSAL_TEXT]


# --- Зона пользователя влияет на «сегодня» --------------------------------


def test_zone_changes_today_in_schedule(session_factory):
    with session_factory() as session:
        repository.create_user(session, 555, TZ_UFA, "21:00", "20:00")
    service = WorkoutService(session_factory, ZoneInfo("UTC"), clock=lambda: NOW)
    # Разовая на 04.11 по UTC; в Екатеринбурге это уже прошлое (05.11).
    service.create_once("04.11", "бег", time(19, 0), today=NOW.date())

    with acting_as(2, TZ_UFA):
        text_local = service.show_schedule()
    text_utc = service.show_schedule()

    assert "04.11" not in text_local
    assert "04.11" in text_utc


def test_zone_changes_wellness_day(session_factory):
    with session_factory() as session:
        repository.create_user(session, 555, TZ_UFA, "21:00", "20:00")
    service = WellnessService(session_factory, ZoneInfo("UTC"), clock=lambda: NOW)

    with acting_as(2, TZ_UFA):
        service.record_command("/wellness 4", NOW)

    with session_factory() as session, acting_as(2, TZ_UFA):
        entry = repository.wellness_for_day(session, date(2026, 11, 5))
        old = repository.wellness_for_day(session, date(2026, 11, 4))
    assert entry is not None and entry.score == 4
    assert old is None
