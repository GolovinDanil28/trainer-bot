"""Клиент Ollama: единственное место, где слой ассистента ходит в сеть.

Модель работает только переводчиком текста в JSON (R-16): в теле запроса нет
ни описаний инструментов, ни доступа к базе. Любой сбой транспорта или ответ
не той формы превращается в `LLMError`, чтобы вызывающий код не различал
httpx-исключения и не менял данные при частичном ответе.
"""

from __future__ import annotations

import json

import httpx

from src.core.assistant import build_system_prompt, build_user_prompt
from src.llm.protocol import LLMError


class OllamaClient:
    """`IntentTranslator` поверх `POST {url}/api/chat`."""

    def __init__(
        self,
        url: str,
        model: str,
        timeout_seconds: float,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        # Клиент можно передать снаружи: в тестах это MockTransport, в бою —
        # соединение живёт один запрос, чтобы не держать пул ради двух вызовов.
        self._client = client

    def _payload(self, text: str, schedule_hint: str) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": build_user_prompt(text, schedule_hint)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        payload = self._payload(text, schedule_hint)
        if self._client is not None:
            return await self._post(self._client, payload)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._post(client, payload)

    async def _post(self, client: httpx.AsyncClient, payload: dict[str, object]) -> str:
        try:
            response = await client.post(f"{self._url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama не ответила за {self._timeout} с") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama недоступна: {exc}") from exc

        if response.status_code != 200:
            raise LLMError(f"Ollama вернула HTTP {response.status_code}")

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("Ollama вернула не JSON") from exc

        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("В ответе Ollama нет поля message.content")
        return content
