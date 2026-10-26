"""Ручные подделки для тестов.

Отдельный модуль, а не копипаста в каждом файле: подделки, размноженные по
тестам, начинают вести себя по-разному в разных файлах, и потом непонятно,
какому из них верить.
"""

from collections import Counter
from types import SimpleNamespace

from src.core.reminders import ReminderMessage
from src.llm.protocol import LLMError

__all__ = [
    "DEFAULT_JSON",
    "DeliveryFailed",
    "FakeBot",
    "FakeCallbackQuery",
    "FakeLLM",
    "FakeMessage",
    "FakeSender",
    "LLMError",
    "make_callback_update",
    "make_context",
    "make_update",
]


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
    """Замена `telegram.Message`: хендлеру нужны `text`, `chat_id` и `reply_text`.

    `events` — общая лента чата: по ней проверяется порядок «печатает…» →
    ответ, а не только факт отправки.
    """

    def __init__(self, text: str = "", events: list[str] | None = None) -> None:
        self.text = text
        self.chat_id = 42
        self.sent: list[str] = []
        self.events = events if events is not None else []

    async def reply_text(self, text: str) -> None:
        self.sent.append(text)
        self.events.append("reply")


class FakeBot:
    """Замена `telegram.Bot`: записывает действия в чат («печатает…»)."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.actions: list[tuple[int, str]] = []
        self.events = events if events is not None else []

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        self.actions.append((chat_id, action))
        self.events.append("typing")


class FakeCallbackQuery:
    """Замена `telegram.CallbackQuery`: нажатие кнопки."""

    def __init__(self, data: str = "") -> None:
        self.data = data
        self.answers: list[str] = []
        self.markup_edits: list[object | None] = []

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text or "")

    async def edit_message_reply_markup(self, reply_markup: object | None = None) -> None:
        self.markup_edits.append(reply_markup)


def make_update(text: str = "", user_id: int = 42, events: list[str] | None = None):
    return SimpleNamespace(
        message=FakeMessage(text, events),
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )


def make_callback_update(data: str, user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=FakeCallbackQuery(data),
    )


def make_context(
    bot_data: dict,
    args: list[str] | None = None,
    bot: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(bot_data=bot_data, args=args or [], bot=bot)


DEFAULT_JSON = '{"action":"none","type":null,"days":null,"time":null,"workout_id":null}'


class FakeLLM:
    """Подделка IntentTranslator: возвращает JSON-ответы или бросает ошибку.

    Режимы (mode):
    - "ok"          — возвращает ответ из словаря answers по тексту
    - "timeout"     — вызывает TimeoutError
    - "unreachable" — вызывает LLMError
    - "invalid_json" — возвращает строку, которая не валидна JSON
    """

    def __init__(
        self,
        answers: dict[str, str] | None = None,
        mode: str = "ok",
        default: str | None = None,
    ) -> None:
        self.answers = answers or {}
        self.mode = mode
        self.default = default or DEFAULT_JSON
        self.calls: list[tuple[str, str]] = []

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        """Вернуть JSON-строку (или бросить ошибку)."""
        self.calls.append((text, schedule_hint))
        if self.mode == "timeout":
            raise TimeoutError("таймаут ожидания ответа от модели")
        if self.mode == "unreachable":
            raise LLMError("Ollama недоступна: соединение разорвано")
        if self.mode == "invalid_json":
            return "не валидный JSON"
        return self.answers.get(
            text,
            self.default,
        )
