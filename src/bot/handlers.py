"""Хендлеры Telegram: тонкие обёртки над сервисом.

Правило слоя: здесь нет ни SQL, ни разбора строк — только «достать текст из
сообщения, вызвать сервис, ответить». Всё, что можно проверить без Telegram,
проверяется в `service.py` и `core/`.
"""

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.service import WorkoutService


def _service(context: ContextTypes.DEFAULT_TYPE) -> WorkoutService:
    return context.bot_data["workout_service"]


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/new strength Пн,Ср,Пт 19:30` — создать тренировку (R-01)."""
    text = update.message.text if update.message else ""
    await update.message.reply_text(_service(context).create_from_command(text or ""))


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/list` — показать активные тренировки (R-02)."""
    await update.message.reply_text(_service(context).show_schedule())


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/off <id>` — выключить тренировку (R-03)."""
    arguments = context.args or []
    if len(arguments) != 1:
        await update.message.reply_text("Нужен номер тренировки. Пример: /off 2")
        return
    await update.message.reply_text(_service(context).disable(arguments[0]))
