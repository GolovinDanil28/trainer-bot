"""Тесты порта IntentTranslator: FakeLLM и режимы поведения.

Каждый тест проверяет один аспект: возврат JSON, ошибки и точный подсчёт вызовов.
Все тесты используют FakeLLM из tests.fakes — они не требуют сети.
"""

import pytest

from tests.fakes import FakeLLM, LLMError


@pytest.mark.asyncio
async def test_fake_returns_canned_answer():
    """Ok-режим: возвращает JSON-строку по ключу."""
    fake = FakeLLM(answers={"привет": '{"action":"list"}'})
    result = await fake.intent_json("привет", "hint")
    assert result == '{"action":"list"}'
    # Call counter увеличился ровно на 1
    assert fake.calls == [("привет", "hint")]


@pytest.mark.asyncio
async def test_fake_uses_default_for_unknown_text():
    """Ok-режим: для неизвестного текста возвращается default JSON."""
    fake = FakeLLM(mode="ok")
    result = await fake.intent_json("никто не знает это", "hint")
    # По умолчанию возвращаем валидный JSON с action none
    assert '"action":"none"' in result
    assert fake.calls == [("никто не знает это", "hint")]


@pytest.mark.asyncio
async def test_fake_timeout_mode_raises():
    """mode=timeout: выбрасывает TimeoutError."""
    fake = FakeLLM(mode="timeout")
    with pytest.raises(TimeoutError, match="таймаут"):
        await fake.intent_json("что угодно", "hint")
    # calls все равно попали, потому что запись происходит до проверки режима
    assert fake.calls == [("что угодно", "hint")]


@pytest.mark.asyncio
async def test_fake_unreachable_mode_raises_llm_error():
    """mode=unreachable: выбрасывает LLMError."""
    fake = FakeLLM(mode="unreachable")
    with pytest.raises(LLMError, match="Ollama недоступна"):
        await fake.intent_json("что угодно", "hint")
    assert fake.calls == [("что угодно", "hint")]


@pytest.mark.asyncio
async def test_fake_invalid_json_mode_returns_garbage():
    """mode=invalid_json: возвращает строку, которая не валидна JSON."""
    fake = FakeLLM(mode="invalid_json")
    result = await fake.intent_json("текст", "hint")
    assert result == "не валидный JSON"
    assert fake.calls == [("текст", "hint")]


@pytest.mark.asyncio
async def test_fake_call_counter_exact():
    """Проверка: calls содержит ровно один кортеж на один вызов."""
    fake = FakeLLM(answers={"a": '{"action":"create"}', "b": '{"action":"none"}'})

    await fake.intent_json("a", "h1")
    await fake.intent_json("b", "h2")

    # Записались оба вызова, порядок сохраняется
    assert fake.calls == [("a", "h1"), ("b", "h2")]
    # len(calls) == 2
    assert len(fake.calls) == 2