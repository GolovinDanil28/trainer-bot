"""Доступ: действия разрешены только владельцу бота (R-05).

В v1 пользователь один, поэтому «владелец» — это id из конфига, а не роль
в базе. Проверка живёт отдельной функцией, чтобы все входы (команды и кнопки)
спрашивали одно и то же.
"""

from telegram import Update
from telegram.ext import ContextTypes

OWNER_KEY = "owner_id"


def is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True, если действие пришло от владельца.

    `False` при отсутствии пользователя — тоже: анонимное действие не может
    быть разрешено «по умолчанию».
    """
    owner_id = context.bot_data.get(OWNER_KEY)
    user = update.effective_user
    return owner_id is not None and user is not None and user.id == owner_id
