"""Тесты клиента Ollama без сети: httpx.MockTransport (2.2, 2.3, R-16)."""

import json

import httpx
import pytest

from src.llm.ollama import OllamaClient
from src.llm.protocol import LLMError

CHAT_URL = "http://ollama.test:11434/api/chat"


def client_with(handler, *, url: str = "http://ollama.test:11434") -> OllamaClient:
    transport = httpx.MockTransport(handler)
    return OllamaClient(url, "qwen2.5:7b", 5, client=httpx.AsyncClient(transport=transport))


async def test_posts_expected_body_and_reads_content():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"action":"list"}'}})

    client = client_with(handler)
    try:
        raw = await client.intent_json("покажи расписание", "#1 — силовая, Пн 19:30")
    finally:
        await client._client.aclose()  # type: ignore[union-attr]

    assert raw == '{"action":"list"}'
    assert captured["url"] == CHAT_URL
    body = captured["body"]
    assert body["model"] == "qwen2.5:7b"
    assert body["stream"] is False
    assert body["format"] == "json"
    assert body["options"] == {"temperature": 0}

    system, user = body["messages"]
    assert system["role"] == "system"
    assert "JSON" in system["content"]
    assert user["role"] == "user"
    assert "покажи расписание" in user["content"]
    assert "#1 — силовая" in user["content"]


def test_request_has_no_tools_or_functions():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "{}"}})

    body = OllamaClient(
        "http://ollama.test:11434",
        "qwen2.5:7b",
        5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )._payload("текст", "снапшот")

    assert set(body) == {"model", "messages", "stream", "format", "options"}
    assert "tools" not in body
    assert "functions" not in body
    assert "tool_calls" not in json.dumps(body)


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(
            lambda request: httpx.ConnectError("нет соединения", request=request),
            id="connect",
        ),
        pytest.param(
            lambda request: httpx.ReadTimeout("долго", request=request),
            id="timeout",
        ),
    ],
)
async def test_transport_failures_become_llm_error(make_error):
    def handler(request: httpx.Request) -> httpx.Response:
        raise make_error(request)

    client = client_with(handler)
    try:
        with pytest.raises(LLMError):
            await client.intent_json("текст", "")
    finally:
        await client._client.aclose()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(httpx.Response(500, text="boom"), id="http-500"),
        pytest.param(httpx.Response(200, content=b"not json"), id="body-not-json"),
        pytest.param(httpx.Response(200, json={"done": True}), id="no-message"),
        pytest.param(httpx.Response(200, json={"message": {"content": ""}}), id="empty-content"),
    ],
)
async def test_bad_response_becomes_llm_error(response):
    client = client_with(lambda request: response)
    try:
        with pytest.raises(LLMError):
            await client.intent_json("текст", "")
    finally:
        await client._client.aclose()  # type: ignore[union-attr]
