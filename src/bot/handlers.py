"""Хендлеры Telegram: тонкие обёртки над сервисом.

Правило слоя: здесь нет ни SQL, ни разбора строк — только «достать текст из
сообщения, вызвать сервис, ответить». Всё, что можно проверить без Telegram,
проверяется в `service.py` и `core/».
"""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.bot.access import owner_only
from src.bot.service import WorkoutService


def _service(context: ContextTypes.DEFAULT_TYPE) -> WorkoutService:
    return context.bot_data["workout_service"]


@owner_only
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/new strength Пн,Ср,Пт 19:30` — создать тренировку (R-01)."""
    text = update.message.text if update.message else ""
    await update.message.reply_text(_service(context).create_from_command(text or ""))


@owner_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/list` — показать активные тренировки (R-02)."""
    await update.message.reply_text(_service(context).show_schedule())


@owner_only
async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/off <id>` — выключить тренировку (R-03)."""
    arguments = context.args or []
    if len(arguments) != 1:
        await update.message.reply_text("Нужен номер тренировки. Пример: /off 2")
        return
    await update.message.reply_text(_service(context).disable(arguments[0]))


@owner_only
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/history` — последние записи журнала (R-13)."""
    await update.message.reply_text(_service(context).show_history())


@owner_only
async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Свободный текст владельца: «печатает…», ответ сервиса, текст ответа (R-14)."""
    message = update.message
    if message is None:
        return
    text = (message.text or "").strip()
    # Фильтр уже отсекает команды, но хендлер обязан быть безопасным и сам:
    # пустое сообщение или `/start` не должны уходить в модель.
    if not text or text.startswith("/"):
        return

    # «Печатает…» — до ожидания модели: при T=20 пользователь видит, что бот
    # работает, а не завис (R-17).
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    assistant = context.bot_data["assistant_service"]
    await message.reply_text(await assistant.handle(text))
