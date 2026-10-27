"""Настоящий транспорт: единственное место, где живёт Telegram API.

Домен объявляет порты (`ReminderSender`, `PromptSender`), а здесь мы их
выполняем. Благодаря этому все тесты доставки работают с подделками, а не с
сетью.
"""

from zoneinfo import ZoneInfo

from telegram import Bot

from src.bot.keyboards import reminder_keyboard, wellness_keyboard
from src.core.messages import format_reminder, format_wellness_prompt
from src.core.reminders import ReminderMessage
from src.core.wellness import WellnessPrompt


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


class TelegramWellnessSender:
    """Отправка вечернего вопроса о самочувствии (R-19)."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send_prompt(self, prompt: WellnessPrompt) -> None:
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=format_wellness_prompt(),
            reply_markup=wellness_keyboard(prompt.day),
        )
