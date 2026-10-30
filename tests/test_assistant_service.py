"""Тесты AssistantService: маппинг в домен, таймаут, повтор, логи (4.2–4.4, 6.1–6.4)."""

import asyncio
import logging
import time
from zoneinfo import ZoneInfo

import pytest

from src.bot.assistant_service import AssistantService
from src.bot.service import WorkoutService
from src.core.messages import (
    format_assistant_fallback,
    format_assistant_unclear,
    format_unknown_workout,
)
from src.db.session import create_db_engine, init_db, make_session_factory
from src.llm.protocol import LLMError
from tests.fakes import FakeLLM

ZONE = ZoneInfo("Asia/Yekaterinburg")
LIST_JSON = '{"action":"list"}'
SNAPSHOT_ROWS = (
    "/new strength Пн,Ср,Пт 19:30",
    "/new cardio Вт,Чт 08:00",
    "/new mobility Вс 10:00",
)


def make_service(db_path) -> WorkoutService:
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return WorkoutService(make_session_factory(engine), ZONE)


def make_assistant(llm, service, timeout: float = 5) -> AssistantService:
    return AssistantService(llm, service, "qwen2.5:7b", timeout)


def seed(service: WorkoutService) -> None:
    for command in SNAPSHOT_ROWS:
        service.create_from_command(command)


async def test_create_gives_same_result_as_strict_command(service, tmp_path):
    phrase = "кардио по вторникам и четвергам в восемь утра"
    llm = FakeLLM(
        answers={
            phrase: '{"action":"create","type":"cardio","days":["Вт","Чт"],"time":"08:00"}'
        }
    )

    answer = await make_assistant(llm, service).handle(phrase)

    direct = make_service(tmp_path / "direct.db")
    assert answer == direct.create_from_command("/new cardio Вт,Чт 08:00")
    assert service.show_schedule() == direct.show_schedule()


async def test_list_returns_schedule(service):
    seed(service)
    phrase = "покажи моё расписание"
    llm = FakeLLM(answers={phrase: LIST_JSON})

    answer = await make_assistant(llm, service).handle(phrase)

    assert answer == service.show_schedule()
    assert "#2 — кардио" in answer


async def test_off_by_ordinal_resolves_through_snapshot(service, tmp_path):
    seed(service)
    snapshot_before = service.schedule_snapshot()
    phrase = "убери вторую тренировку"
    llm = FakeLLM(answers={phrase: '{"action":"off","workout_id":2}'})

    answer = await make_assistant(llm, service).handle(phrase)

    # Снапшот ушёл модели — иначе порядковый номер не с чем сопоставить (D4).
    assert llm.calls == [(phrase, snapshot_before)]

    direct = make_service(tmp_path / "direct.db")
    seed(direct)
    assert answer == direct.disable("2")
    assert "#2" not in service.show_schedule()


async def test_off_unknown_id_returns_domain_error(service):
    seed(service)
    phrase = "убери тренировку 99"
    llm = FakeLLM(answers={phrase: '{"action":"off","workout_id":99}'})

    answer = await make_assistant(llm, service).handle(phrase)

    assert answer == format_unknown_workout(99)
    assert "#1" in service.show_schedule()


async def test_none_does_not_touch_write_methods(service):
    def boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("none не должен вызывать запись")

    service.create_from_command = boom  # type: ignore[method-assign]
    service.create_weekly = boom  # type: ignore[method-assign]
    service.create_once = boom  # type: ignore[method-assign]
    service.show_schedule = boom  # type: ignore[method-assign]
    service.disable = boom  # type: ignore[method-assign]

    answer = await make_assistant(FakeLLM(), service).handle("я устал")

    assert answer == format_assistant_unclear()


class FailOnceLLM:
    """Первый вызов — сбой, второй — валидный ответ."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        self.calls += 1
        if self.calls == 1:
            raise LLMError("соединение разорвано")
        return self.payload


class SlowLLM:
    """Отвечает медленнее таймаута — проверка ограничения времени (6.1)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return LIST_JSON


class PerTextLLM:
    """Разная задержка по тексту — проверка неблокирующей обработки (6.3)."""

    def __init__(self, delays: dict[str, float]) -> None:
        self.delays = delays

    async def intent_json(self, text: str, schedule_hint: str) -> str:
        await asyncio.sleep(self.delays[text])
        return LIST_JSON


async def test_second_attempt_succeeds_and_action_runs(service):
    phrase = "поставь кардио на вторник в 8 утра"
    llm = FailOnceLLM(
        '{"action":"create","type":"cardio","days":["Вт"],"time":"08:00"}'
    )

    answer = await make_assistant(llm, service).handle(phrase)

    assert llm.calls == 2
    assert answer.startswith("Готово: #1 — кардио, Вт, 08:00")
    assert "#1" in service.show_schedule()


async def test_two_timeouts_stay_within_double_timeout(service):
    timeout = 0.05
    llm = SlowLLM(delay=5)

    started = time.monotonic()
    answer = await make_assistant(llm, service, timeout=timeout).handle("покажи")
    elapsed = time.monotonic() - started

    assert llm.calls == 2
    assert answer == format_assistant_fallback()
    assert elapsed <= 2 * timeout + 0.1


async def test_slow_message_does_not_block_fast_one(service):
    llm = PerTextLLM({"медленно": 0.3, "быстро": 0.0})
    assistant = make_assistant(llm, service, timeout=2)
    finished: list[str] = []

    async def run(text: str) -> None:
        await assistant.handle(text)
        finished.append(text)

    await asyncio.gather(run("медленно"), run("быстро"))

    assert finished == ["быстро", "медленно"]


@pytest.mark.parametrize(
    ("answer", "mode", "outcome"),
    [
        pytest.param('{"action":"list"}', "ok", "ok", id="ok"),
        pytest.param(None, "timeout", "timeout", id="timeout"),
        pytest.param(None, "unreachable", "unreachable", id="unreachable"),
        pytest.param(None, "invalid_json", "invalid_response", id="invalid-json"),
        pytest.param('{"action":"delete_all"}', "ok", "invalid_response", id="bad-action"),
        pytest.param(
            '{"action":"create","type":"cardio","days":["Вт"]}',
            "ok",
            "invalid_response",
            id="missing-time",
        ),
        pytest.param(
            '{"action":"create","type":"cardio","days":["Вт"],"time":"25:00"}',
            "ok",
            "invalid_response",
            id="bad-time",
        ),
        pytest.param(
            '{"action":"create","type":"cardio","days":["Сббота"],"time":"08:00"}',
            "ok",
            "invalid_response",
            id="bad-day",
        ),
    ],
)
async def test_failures_do_not_change_state_and_log_fields(
    service, caplog, answer, mode, outcome
):
    seed(service)
    before = service.show_schedule()
    llm = FakeLLM(answers={"фраза": answer} if answer else None, mode=mode)
    if mode == "ok" and answer is None:
        raise AssertionError("для режима ok нужен ответ")

    with caplog.at_level(logging.INFO, logger="src.bot.assistant_service"):
        reply = await make_assistant(llm, service).handle("фраза")

    assert service.show_schedule() == before
    record = caplog.records[-1]
    assert record.outcome == outcome
    assert record.model == "qwen2.5:7b"
    assert record.attempts in (1, 2)
    assert isinstance(record.latency_ms, int)
    assert "фраза" not in caplog.text
    if outcome in {"timeout", "unreachable", "invalid_response"}:
        assert reply == format_assistant_fallback()
        assert "/new" in reply
