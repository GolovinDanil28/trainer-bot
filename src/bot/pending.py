"""Уточнение выбора типа: короткоживущее состояние bot-слоя (R-34).

Почему не в БД: у уточнения нет будущих последствий — если процесс
перезапустился, владелец просто формулирует фразу заново. Таблица ради
состояния, которое живёт десять минут, дороже, чем потеря одного уточнения.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from uuid import uuid4

from src.core.assistant import AssistantIntent

TTL_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class PendingChoice:
    """Запомненное намерение, ждущее выбора владельца."""

    id: str
    chat_id: int
    intent: AssistantIntent
    created_at: float


class ChoiceStatus(StrEnum):
    """Чем закончился поиск уточнения по кнопке."""

    PENDING = "pending"
    DONE = "done"
    MISSING = "missing"


class PendingChoices:
    """Набор уточнений процесса: положить, забрать, отметить выполненным."""

    def __init__(
        self,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, PendingChoice] = {}
        self._done: dict[str, float] = {}

    def put(self, chat_id: int, intent: AssistantIntent) -> PendingChoice:
        self._prune()
        choice = PendingChoice(
            id=uuid4().hex[:8],
            chat_id=chat_id,
            intent=intent,
            created_at=self._clock(),
        )
        self._items[choice.id] = choice
        return choice

    def take(self, pending_id: str) -> tuple[PendingChoice | None, ChoiceStatus]:
        """Забрать уточнение. Повторный клик по созданной — `DONE`."""
        self._prune()
        choice = self._items.pop(pending_id, None)
        if choice is not None:
            return choice, ChoiceStatus.PENDING
        if pending_id in self._done:
            return None, ChoiceStatus.DONE
        return None, ChoiceStatus.MISSING

    def mark_done(self, pending_id: str) -> None:
        self._done[pending_id] = self._clock()

    def _prune(self) -> None:
        deadline = self._clock() - self._ttl
        for key, item in list(self._items.items()):
            if item.created_at < deadline:
                del self._items[key]
        for key, created_at in list(self._done.items()):
            if created_at < deadline:
                del self._done[key]
