"""Доступ: действия разрешены только владельцу бота (R-05).

В v1 пользователь один, поэтому «владелец» — это id из конфига, а не роль
в базе. Проверка живёт отдельной функцией, чтобы все входы (команды и кнопки)
спрашивали одно и то же.
"""

from collections.abc import Awaitable, Callable
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

OWNER_KEY = "owner_id"

# Нейтральный отказ (R-05): ни названий тренировок, ни истории, ни намёка,
# что расписание вообще существует. Чужой не должен узнать о боте больше,
# чем «здесь что-то есть».
REFUSAL_TEXT = "Это личный бот: команды доступны только владельцу."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True, если действие пришло от владельца.

    `False` при отсутствии пользователя — тоже: анонимное действие не может
    быть разрешено «по умолчанию».
    """
    owner_id = context.bot_data.get(OWNER_KEY)
    user = update.effective_user
    return owner_id is not None and user is not None and user.id == owner_id


def owner_only(handler: Handler) -> Handler:
    """Пускает к хендлеру только владельца (R-05).

    Один декоратор на все команды — чтобы новую команду нельзя было случайно
    оставить открытой: доступ проверяется там же, где регистрируется поведение,
    а не в отдельном списке, который легко забыть обновить.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_owner(update, context):
            await handler(update, context)
            return

        # Команды всегда приходят с сообщением; проверка нужна, чтобы декоратор
        # нельзя было уронить, повесив его на callback без message.
        if update.message is not None:
            await update.message.reply_text(REFUSAL_TEXT)

    return wrapper
