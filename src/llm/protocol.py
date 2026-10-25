"""Порт перевода свободного текста в структурированное намерение.

Определяет контракт `IntentTranslator`, который реализует LLM-клиент (Ollama
или простая замена в тестах). `LLMError` выбрасывается при недоступности модели
или когда ответ не читается как JSON.

Примеры реализаций:
- `src/llm/ollama.py` — реальный клиент через httpx (будет в следующем change).
- `tests/fakes.FakeLLM` — подделка для тестов без сети.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class LLMError(Exception):
    """Модель недоступна или ответила не тем, что умеем разобрать."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@runtime_checkable
class IntentTranslator(Protocol):
    """Протокол перевода текста в JSON.

    Реальная реализация возвращает валидный JSON-строку.
    Подделка в тестах может возвращать любой текст или выбрасывать ошибки.
    """

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        """Вернуть JSON-ответ модели.

        Args:
            text: свободный текст пользователя (одно сообщение).
            schedule_hint: читаемый снапшот текущего расписания (может быть пустым).

        Returns:
            Строка с JSON-объектом.

        Raises:
            LLMError: модель недоступна (сервер не отвечает, таймаут).
            TimeoutError: истёк таймаут ожидания ответа.
        """
        ...