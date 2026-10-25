"""Настоящий транспорт: единственное место, где живёт Telegram API.

Домен объявляет `ReminderSender`, а здесь мы его выполняем. Благодаря этому
все тесты доставки работают с `FakeSender`, а не с сетью.
"""

from zoneinfo import ZoneInfo

from telegram import Bot

from src.bot.keyboards import reminder_keyboard
from src.core.messages import format_reminder
from src.core.reminders import ReminderMessage


class TelegramSender:
    """Отправка напоминания владельцу бота."""

    def __init__(self, bot: Bot, chat_id: int, zone: ZoneInfo) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._zone = zone

    async def send(self, message: ReminderMessage) -> None:
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=format_reminder(message, self._zone),
            reply_markup=reminder_keyboard(message.reminder_id),
        )
