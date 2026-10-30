"""Сервис ассистента: фраза владельца → операция домена.

Здесь сходятся три части, которые по отдельности уже проверены: промпты и
валидатор из `core/assistant.py`, порт `IntentTranslator` из `llm/` и
`WorkoutService`. Своей записи в базу у сервиса нет — только вызовы
существующих методов, поэтому свободный текст не может создать состояние,
недостижимое строгой командой (R-16, D6).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import time as clock_time

from src.bot.service import WorkoutService
from src.core.assistant import (
    AssistantAction,
    AssistantIntent,
    IntentError,
    parse_intent,
)
from src.core.messages import (
    format_assistant_fallback,
    format_assistant_unclear,
    format_type_choice,
)
from src.llm.protocol import IntentTranslator, LLMError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """Ответ на фразу и намерение, если нужен выбор владельца (R-34)."""

    text: str
    intent: AssistantIntent | None = None


class AssistantService:
    """Перевод фразы в намерение с таймаутом, одним повтором и логом (D5, D8)."""

    def __init__(
        self,
        translator: IntentTranslator,
        workouts: WorkoutService,
        model_name: str,
        timeout_seconds: float,
        *,
        max_attempts: int = 2,
    ) -> None:
        self._translator = translator
        self._workouts = workouts
        self._model = model_name
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts

    async def handle(self, text: str) -> str:
        """Текст ответа без кнопок: прежний контракт для тестов и вызовов."""
        return (await self.handle_turn(text)).text

    async def handle_turn(self, text: str) -> AssistantTurn:
        """Ответ и намерение, если владельцу нужно выбрать вариант типа."""
        started = time.monotonic()
        # Снапшот читается нашим кодом: модель видит текст, но не имеет доступа
        # к базе (R-16). Он же нужен для фраз «убери вторую тренировку» (D4).
        snapshot = self._workouts.schedule_snapshot()
        attempts, raw, failure = await self._translate(text, snapshot)

        if failure is not None:
            answer = format_assistant_fallback()
            outcome, action = failure, None
        else:
            try:
                intent = parse_intent(raw, text)
            except IntentError:
                intent = None

            if intent is None:
                answer = format_assistant_fallback()
                outcome, action = "invalid_response", None
            elif intent.action is AssistantAction.NONE:
                answer = format_assistant_unclear()
                outcome, action = "refused", intent.action.value
            elif intent.action is AssistantAction.CREATE and intent.type_options:
                # Ничего не создаём: во фразе было «или» (R-34). База остаётся
                # нетронутой до выбора владельца.
                answer = format_type_choice(intent.type_options)
                outcome, action = "choice", intent.action.value
                self._log(outcome, action, attempts, started)
                return AssistantTurn(text=answer, intent=intent)
            else:
                answer = self._apply(intent)
                outcome, action = "ok", intent.action.value

        self._log(outcome, action, attempts, started)
        return AssistantTurn(text=answer)

    async def _translate(
        self, text: str, snapshot: str
    ) -> tuple[int, str | None, str | None]:
        """Вернуть (попытки, сырой ответ, вид сбоя). Повтор — ровно один (D5)."""
        last_error: Exception | None = None
        attempts = 0
        for attempts in range(1, self._max_attempts + 1):
            try:
                raw = await asyncio.wait_for(
                    self._translator.intent_json(text, snapshot),
                    timeout=self._timeout,
                )
                return attempts, raw, None
            except TimeoutError as exc:
                last_error = exc
            except LLMError as exc:
                last_error = exc

        failure = "timeout" if isinstance(last_error, TimeoutError) else "unreachable"
        return attempts, None, failure

    def _apply(self, intent: AssistantIntent) -> str:
        """Выполнить намерение существующими методами домена (D6)."""
        if intent.action is AssistantAction.CREATE:
            # Тот же путь, что у `/new`, но без сборки строки команды: тип может
            # быть фразой с пробелом (R-33), и такая строка уже не разберётся.
            # Правила длительности общие — через `make_weekly_draft`.
            moment = clock_time.fromisoformat(intent.time or "00:00")
            if intent.date_raw is not None:
                # Разовая тренировка: дату считает WorkoutService в таймзоне
                # владельца, модель отдала только токен (D2).
                return self._workouts.create_once(
                    intent.date_raw,
                    intent.type or "",
                    moment,
                    intent.distance_km,
                    intent.duration_min,
                )
            return self._workouts.create_weekly(
                intent.type or "",
                tuple(intent.days or ()),
                moment,
                intent.distance_km,
                intent.duration_min,
            )

        if intent.action is AssistantAction.LIST:
            return self._workouts.show_schedule()

        return self._workouts.disable(str(intent.workout_id))

    def _log(
        self, outcome: str, action: str | None, attempts: int, started: float
    ) -> None:
        """Одна запись на сообщение; текст фразы и промпт не логируются (D8)."""
        logger.info(
            "assistant",
            extra={
                "outcome": outcome,
                "action": action,
                "model": self._model,
                "attempts": attempts,
                "latency_ms": int((time.monotonic() - started) * 1000),
            },
        )
