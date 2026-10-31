"""Доступ: действия разрешены только зарегистрированным пользователям (R-05, R-41).

Кто зарегистрирован — решает база (`user.telegram_id`), а не конфиг. Владелец
из `OWNER_ID` остаётся только для первого запуска и (в следующем change)
инвайт-кода; данные он видит как обычный пользователь.

Декоратор заодно объявляет текущего пользователя (`acting_as`), поэтому
сервисы и репозиторий ниже получают владельца без протаскивания id через
каждую подпись.
"""

from collections.abc import Awaitable, Callable
from functools import wraps

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from src.db.context import DEFAULT_USER_ID, acting_as
from src.db.models import User

OWNER_KEY = "owner_id"
SESSION_FACTORY_KEY = "session_factory"

# Нейтральный отказ (R-05): ни названий тренировок, ни истории, ни намёка,
# что расписание вообще существует.
REFUSAL_TEXT = "Это личный бот: команды доступны только зарегистрированным."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def resolve_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """id пользователя в базе или `None`, если он не зарегистрирован.

    Владелец из конфига считается зарегистрированным всегда: он получает
    пользователя по умолчанию даже если строки ещё нет (первый запуск).
    """
    telegram = update.effective_user
    if telegram is None:
        return None

    factory = context.bot_data.get(SESSION_FACTORY_KEY)
    if factory is not None:
        with factory() as session:
            user_id = session.scalar(
                select(User.id).where(
                    User.telegram_id == telegram.id,
                    User.is_active.is_(True),
                )
            )
        if user_id is not None:
            return int(user_id)
        # Фабрика есть, но строки нет — это посторонний, а не «владелец».
        return None

    # Без фабрики (тесты хендлеров) работает только запасной путь владельца.
    owner_id = context.bot_data.get(OWNER_KEY)
    if owner_id is not None and telegram.id == owner_id:
        return DEFAULT_USER_ID
    return None


def is_registered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True, если действие пришло от зарегистрированного пользователя."""
    return resolve_user_id(update, context) is not None


def registered_only(handler: Handler) -> Handler:
    """Пускает к хендлеру только зарегистрированного и объявляет владельца.

    Один декоратор на все входы — чтобы новую команду нельзя было случайно
    оставить открытой, а забыть объявить пользователя — нельзя было вовсе.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = resolve_user_id(update, context)
        if user_id is not None:
            with acting_as(user_id):
                await handler(update, context)
            return

        # Команды всегда приходят с сообщением; проверка нужна, чтобы декоратор
        # нельзя было уронить, повесив его на callback без message.
        if update.message is not None:
            await update.message.reply_text(REFUSAL_TEXT)

    return wrapper


# Прежнее имя оставлено для тестов и читателей: «владелец» теперь значит
# «зарегистрированный».
owner_only = registered_only
