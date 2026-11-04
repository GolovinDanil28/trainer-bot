"""Регистрация по инвайт-коду (R-44): вход, отказ, идемпотентность."""

from sqlalchemy import select

from src.bot.access import (
    OWNER_KEY,
    REFUSAL_TEXT,
    REGISTRATION_KEY,
    SESSION_FACTORY_KEY,
)
from src.bot.handlers import cmd_list, cmd_start
from src.bot.user_service import UserService
from src.core.messages import (
    REGISTRATION_CLOSED,
    REGISTRATION_HINT,
    format_help,
)
from src.core.users import Registration
from src.db.models import User
from tests.fakes import make_context, make_update

CODE = "family-42"


def registration(code: str = CODE) -> Registration:
    return Registration(
        invite_code=code,
        tz="Asia/Yekaterinburg",
        wellness_time="21:30",
        week_review_time="20:15",
    )


def context(session_factory, reg: Registration | None = None, args=None):
    rules = reg or registration()
    return make_context(
        {
            SESSION_FACTORY_KEY: session_factory,
            REGISTRATION_KEY: rules,
            "user_service": UserService(session_factory, rules),
            OWNER_KEY: 42,
        },
        args=args,
    )


def users(session_factory) -> list[User]:
    with session_factory() as session:
        return list(session.scalars(select(User).order_by(User.id)))


async def test_valid_code_registers_with_defaults(session_factory):
    update = make_update(f"/start {CODE}", user_id=555)

    await cmd_start(update, context(session_factory, args=[CODE]))

    created = users(session_factory)
    assert len(created) == 2  # пользователь по умолчанию + новый
    new = created[-1]
    assert (new.telegram_id, new.tz, new.wellness_time, new.week_review_time) == (
        555, "Asia/Yekaterinburg", "21:30", "20:15"
    )
    assert format_help() in update.message.sent[0]


async def test_wrong_code_creates_nothing(session_factory):
    update = make_update("/start nope", user_id=555)

    await cmd_start(update, context(session_factory, args=["nope"]))

    assert update.message.sent == [REGISTRATION_HINT]
    assert len(users(session_factory)) == 1


async def test_disabled_registration(session_factory):
    update = make_update("/start nope", user_id=555)

    await cmd_start(update, context(session_factory, registration(code="")))

    assert update.message.sent == [REGISTRATION_CLOSED]
    assert len(users(session_factory)) == 1


async def test_repeat_registration_is_idempotent(session_factory):
    update = make_update(f"/start {CODE}", user_id=555)
    ctx = context(session_factory, args=[CODE])

    await cmd_start(update, ctx)
    await cmd_start(update, ctx)

    assert len([u for u in users(session_factory) if u.telegram_id == 555]) == 1


async def test_registered_user_gets_card_without_code(session_factory):
    await cmd_start(
        make_update(f"/start {CODE}", user_id=555),
        context(session_factory, args=[CODE]),
    )

    update = make_update("/start", user_id=555)
    await cmd_start(update, context(session_factory))

    assert format_help() in update.message.sent[0]


async def test_stranger_commands_still_refused(session_factory):
    update = make_update("/list", user_id=555)

    await cmd_list(update, context(session_factory))

    assert update.message.sent == [REFUSAL_TEXT]


async def test_disabled_user_is_refused(session_factory):
    await cmd_start(
        make_update(f"/start {CODE}", user_id=555),
        context(session_factory, args=[CODE]),
    )
    with session_factory() as session:
        user = session.scalar(select(User).where(User.telegram_id == 555))
        assert user is not None
        user.is_active = False
        session.commit()

    update = make_update("/list", user_id=555)
    await cmd_list(update, context(session_factory))

    assert update.message.sent == [REFUSAL_TEXT]
