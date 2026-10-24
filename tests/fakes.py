"""Ручные подделки для тестов.

Отдельный модуль, а не копипаста в каждом файле: подделки, размноженные по
тестам, начинают вести себя по-разному в разных файлах, и потом непонятно,
какому из них верить.
"""

from collections import Counter
from types import SimpleNamespace

from src.core.reminders import ReminderMessage


class DeliveryFailed(RuntimeError):
    """Имитация ошибки Telegram API."""


class FakeSender:
    """Транспорт, который записывает отправленное и умеет падать.

    `failures` — сколько первых попыток упадёт (проверка повторов).
    `fail_for` — id напоминаний, которые не доставляются никогда (проверка
    того, что одно упавшее напоминание не останавливает остальные).
    """

    def __init__(self, failures: int = 0, fail_for: set[int] | None = None) -> None:
        self._failures_left = failures
        self._fail_for = fail_for or set()
        self.sent: list[ReminderMessage] = []
        self.attempts = 0
        self.attempts_by_reminder: Counter[int] = Counter()

    async def send(self, message: ReminderMessage) -> None:
        self.attempts += 1
        self.attempts_by_reminder[message.reminder_id] += 1
        if message.reminder_id in self._fail_for:
            raise DeliveryFailed(f"напоминание {message.reminder_id} не доставляется")
        if self._failures_left > 0:
            self._failures_left -= 1
            raise DeliveryFailed("Telegram недоступен")
        self.sent.append(message)


class FakeMessage:
    """Замена `telegram.Message`: хендлеру нужны только `text` и `reply_text`."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.sent.append(text)


def make_update(text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        message=FakeMessage(text),
        effective_user=SimpleNamespace(id=42),
    )


def make_context(bot_data: dict, args: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(bot_data=bot_data, args=args or [])
