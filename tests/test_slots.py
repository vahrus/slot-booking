import datetime
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database.database import Base, create_sqlite_engine, get_db
from app.database.models import Slot
from app.routers import admin, public
from app.schemas.slot import SlotCreate
from app.services.slots import (
    SlotAlreadyExistsError,
    create_slot,
    get_slots_by_date,
)


@pytest.fixture
def session_factory(
    tmp_path: Path,
) -> Iterator[sessionmaker[Session]]:
    database_url = f"sqlite:///{tmp_path / 'slots-test.db'}"
    test_engine = create_sqlite_engine(database_url)
    Base.metadata.create_all(bind=test_engine)
    factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    yield factory

    test_engine.dispose()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    test_app.mount("/static", StaticFiles(directory=static_dir), name="static")
    test_app.include_router(public.router)
    test_app.include_router(admin.router)
    test_app.dependency_overrides[get_db] = override_get_db

    with TestClient(test_app) as test_client:
        yield test_client


def slot_data(
    time_value: datetime.time = datetime.time(10, 0),
    date_value: datetime.date = datetime.date(2026, 10, 10),
) -> SlotCreate:
    return SlotCreate(date=date_value, time=time_value)


def test_create_slot_service(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slot = create_slot(session, slot_data())

        assert slot.id is not None
        assert slot.date == datetime.date(2026, 10, 10)
        assert slot.time == datetime.time(10, 0)
        assert slot.status == "free"


def test_duplicate_slot_service_rolls_back(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        create_slot(session, slot_data())

        with pytest.raises(SlotAlreadyExistsError):
            create_slot(session, slot_data())

        remaining = session.scalars(select(Slot)).all()
        assert len(remaining) == 1
        assert remaining[0].time == datetime.time(10, 0)


def test_get_slots_filters_by_date(
    session_factory: sessionmaker[Session],
) -> None:
    requested_date = datetime.date(2026, 10, 10)
    other_date = datetime.date(2026, 10, 11)

    with session_factory() as session:
        create_slot(session, slot_data(date_value=requested_date))
        create_slot(session, slot_data(date_value=other_date))

        slots = get_slots_by_date(session, requested_date)

        assert len(slots) == 1
        assert slots[0].date == requested_date


def test_get_slots_sorts_by_time(
    session_factory: sessionmaker[Session],
) -> None:
    times = [
        datetime.time(14, 0),
        datetime.time(9, 0),
        datetime.time(12, 30),
        datetime.time(10, 0),
    ]

    with session_factory() as session:
        for time_value in times:
            create_slot(session, slot_data(time_value=time_value))

        slots = get_slots_by_date(
            session,
            datetime.date(2026, 10, 10),
        )

        assert [slot.time.strftime("%H:%M") for slot in slots] == [
            "09:00",
            "10:00",
            "12:30",
            "14:00",
        ]


def test_get_slots_returns_empty_list(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        slots = get_slots_by_date(
            session,
            datetime.date(2026, 12, 31),
        )

        assert slots == []


def test_homepage_returns_200(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Выберите дату" in response.text


def test_homepage_shows_only_selected_date(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        create_slot(session, slot_data(time_value=datetime.time(9, 0)))
        create_slot(
            session,
            slot_data(
                time_value=datetime.time(15, 0),
                date_value=datetime.date(2026, 10, 11),
            ),
        )

    response = client.get("/?date=2026-10-10")

    assert response.status_code == 200
    assert "10 октября 2026" in response.text
    assert "09:00" in response.text
    assert "15:00" not in response.text


def test_admin_returns_200(client: TestClient) -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert "Создать слот" in response.text


def test_create_slot_via_form_uses_redirect(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    response = client.post(
        "/admin/slots",
        data={"date": "2026-10-10", "time": "10:00"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin?")

    final_response = client.get(response.headers["location"])
    assert final_response.status_code == 200
    assert "Слот 10:00 на 10.10.2026 создан." in final_response.text

    with session_factory() as session:
        slot_count = session.scalar(select(func.count()).select_from(Slot))
        assert slot_count == 1


def test_duplicate_slot_via_form_shows_error(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    form_data = {"date": "2026-10-10", "time": "10:00"}
    client.post("/admin/slots", data=form_data)

    response = client.post(
        "/admin/slots",
        data=form_data,
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Слот на выбранные дату и время уже существует." in response.text

    with session_factory() as session:
        slot_count = session.scalar(select(func.count()).select_from(Slot))
        assert slot_count == 1


def test_invalid_homepage_date_is_handled(client: TestClient) -> None:
    response = client.get("/?date=not-a-date")

    assert response.status_code == 200
    assert "Укажите корректную дату." in response.text


def test_invalid_slot_form_is_handled(client: TestClient) -> None:
    response = client.post(
        "/admin/slots",
        data={"date": "", "time": "not-a-time"},
    )

    assert response.status_code == 400
    assert "Укажите корректные дату и время." in response.text
