"""Корпус из 20 фраз: 12 командных и 8 отказных (7.1–7.2) и сквозной путь (7.3).

Фразы и ожидания — из `design.md`, D10. Модель заменена `FakeLLM`: здесь
проверяется наша сторона — маппинг в домен и неизменность данных при отказе.
"""

from zoneinfo import ZoneInfo

import pytest

from src.bot.assistant_service import AssistantService
from src.bot.service import WorkoutService
from src.core.messages import format_assistant_unclear
from src.core.schedule import Weekday, WorkoutType
from src.db import repository
from src.db.session import create_db_engine, init_db, make_session_factory
from tests.fakes import FakeLLM

ZONE = ZoneInfo("Asia/Yekaterinburg")

SNAPSHOT_ROWS = (
    "/new strength Пн,Ср,Пт 19:30",
    "/new cardio Вт,Чт 08:00",
    "/new mobility Вс 10:00",
)


def make_service(db_path) -> WorkoutService:
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    return WorkoutService(make_session_factory(engine), ZONE)


def make_assistant(answers: dict[str, str], service: WorkoutService) -> AssistantService:
    return AssistantService(FakeLLM(answers=answers), service, "qwen2.5:7b", 5)


def create_json(workout_type: str, days: list[str], moment: str) -> str:
    days_json = ", ".join(f'"{day}"' for day in days)
    return (
        f'{{"action":"create","type":"{workout_type}",'
        f'"days":[{days_json}],"time":"{moment}"}}'
    )


def seed(service: WorkoutService) -> None:
    for command in SNAPSHOT_ROWS:
        service.create_from_command(command)


CREATE_CASES = [
    pytest.param(
        "кардио по вторникам и четвергам в восемь утра",
        create_json("cardio", ["Вт", "Чт"], "08:00"),
        WorkoutType.CARDIO,
        [Weekday.TUESDAY, Weekday.THURSDAY],
        "08:00",
        id="1-cardio-2-days",
    ),
    pytest.param(
        "силовая пн ср пт в 19:30",
        create_json("strength", ["Пн", "Ср", "Пт"], "19:30"),
        WorkoutType.STRENGTH,
        [Weekday.MONDAY, Weekday.WEDNESDAY, Weekday.FRIDAY],
        "19:30",
        id="2-strength-3-days",
    ),
    pytest.param(
        "хочу мобильность по воскресеньям в 10 утра",
        create_json("mobility", ["Вс"], "10:00"),
        WorkoutType.MOBILITY,
        [Weekday.SUNDAY],
        "10:00",
        id="3-mobility-sunday",
    ),
    pytest.param(
        "поставь силовую на понедельник и пятницу на семь вечера",
        create_json("strength", ["Пн", "Пт"], "19:00"),
        WorkoutType.STRENGTH,
        [Weekday.MONDAY, Weekday.FRIDAY],
        "19:00",
        id="4-strength-evening",
    ),
    pytest.param(
        "кардио каждый день в 7:00",
        create_json("cardio", ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], "07:00"),
        WorkoutType.CARDIO,
        list(Weekday),
        "07:00",
        id="5-cardio-every-day",
    ),
    pytest.param(
        "кардио по вторникам в 8 вечера",
        create_json("cardio", ["Вт"], "20:00"),
        WorkoutType.CARDIO,
        [Weekday.TUESDAY],
        "20:00",
        id="14-cardio-evening",
    ),
    pytest.param(
        "добавь кардио на субботу в 9:30",
        create_json("cardio", ["Сб"], "09:30"),
        WorkoutType.CARDIO,
        [Weekday.SATURDAY],
        "09:30",
        id="18-cardio-saturday",
    ),
    pytest.param(
        "кардио вт чт сб в 6 вечера",
        create_json("cardio", ["Вт", "Чт", "Сб"], "18:00"),
        WorkoutType.CARDIO,
        [Weekday.TUESDAY, Weekday.THURSDAY, Weekday.SATURDAY],
        "18:00",
        id="20-cardio-3-days",
    ),
]


@pytest.mark.parametrize(
    ("phrase", "answer", "workout_type", "days", "moment"), CREATE_CASES
)
async def test_create_phrases(service, phrase, answer, workout_type, days, moment):
    await make_assistant({phrase: answer}, service).handle(phrase)

    with service._session_factory() as session:
        workouts = repository.list_active(session)

    assert len(workouts) == 1
    workout = workouts[0]
    assert workout.workout_type == workout_type
    assert list(workout.weekdays) == days
    assert workout.time_of_day.strftime("%H:%M") == moment


@pytest.mark.parametrize(
    "phrase",
    ["покажи моё расписание", "что у меня по тренировкам?"],
    ids=["6-show", "7-what"],
)
async def test_list_phrases(service, phrase):
    seed(service)

    answer = await make_assistant({phrase: '{"action":"list"}'}, service).handle(phrase)

    assert answer == service.show_schedule()
    assert "#3 — мобильность" in answer


@pytest.mark.parametrize(
    "phrase",
    ["выключи тренировку 2", "убери вторую тренировку"],
    ids=["8-by-id", "9-by-ordinal"],
)
async def test_off_phrases(service, phrase):
    seed(service)

    answer = await make_assistant(
        {phrase: '{"action":"off","workout_id":2}'}, service
    ).handle(phrase)

    assert answer == "Тренировка #2 отключена. История отметок сохранена."
    assert "#2" not in service.show_schedule()


REFUSAL_CASES = [
    pytest.param("завтра не смогу", '{"action":"none","reason":"разовый перенос"}', id="10"),
    pytest.param("я устал", '{"action":"none","reason":"самочувствие"}', id="11"),
    pytest.param("как дела", '{"action":"none","reason":"не про расписание"}', id="12"),
    pytest.param("силовая в 25:00", '{"action":"none","reason":"невалидное время"}', id="13"),
    pytest.param("мобильность в среду утром", '{"action":"none","reason":"нет времени"}', id="15"),
    pytest.param("тренировка завтра в 18:00", '{"action":"none","reason":"разовое"}', id="16"),
    pytest.param("силовая по чётным дням", '{"action":"none","reason":"невыразимо"}', id="17"),
    pytest.param(
        "перенеси силовую с понедельника на вторник",
        '{"action":"none","reason":"перенос"}',
        id="19",
    ),
]


@pytest.mark.parametrize(("phrase", "answer"), REFUSAL_CASES)
async def test_refusal_phrases_do_not_change_state(service, phrase, answer):
    seed(service)
    before = service.show_schedule()

    reply = await make_assistant({phrase: answer}, service).handle(phrase)

    assert reply == format_assistant_unclear()
    assert service.show_schedule() == before


async def test_text_path_matches_command_path_end_to_end(service, tmp_path):
    phrases = {
        "поставь кардио на вторник в 8 утра": create_json("cardio", ["Вт"], "08:00"),
        "покажи расписание": '{"action":"list"}',
        "убери первую тренировку": '{"action":"off","workout_id":1}',
    }
    assistant = make_assistant(phrases, service)
    direct = make_service(tmp_path / "direct.db")

    assert await assistant.handle("поставь кардио на вторник в 8 утра") == (
        direct.create_from_command("/new cardio Вт 08:00")
    )
    assert service.show_schedule() == direct.show_schedule()

    assert await assistant.handle("покажи расписание") == direct.show_schedule()

    assert await assistant.handle("убери первую тренировку") == direct.disable("1")
    assert service.show_schedule() == direct.show_schedule()
