import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.booking import BookingCreate
from app.services.bookings import (
    BookingCancellationError,
    BookingConflictError,
    BookingNotFoundError,
    SlotNotAvailableError,
    SlotNotFoundError,
    cancel_booking,
    create_booking,
    get_booking_by_id,
)
from app.services.slots import get_slot_by_id, get_slots_by_date


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
_RU_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)
_CANCEL_SOURCES = {"public", "admin"}


def format_ru_date(value: datetime.date) -> str:
    """Format a date as a readable Russian phrase."""
    return f"{value.day} {_RU_MONTHS[value.month - 1]} {value.year}"


def _schedule_url(selected_date: datetime.date | None) -> str:
    if selected_date is None:
        return "/"
    return "/?" + urlencode({"date": selected_date.isoformat()})


def _cancel_result_url(selected_date: datetime.date, source: str) -> str:
    if source == "admin":
        return "/admin?" + urlencode(
            {"date": selected_date.isoformat(), "cancelled": "1"}
        )
    return "/?" + urlencode(
        {"date": selected_date.isoformat(), "cancelled": "1"}
    )


def _normalize_cancel_source(source: str | None) -> str:
    if source in _CANCEL_SOURCES:
        return source
    return "public"


def _user_facing_error(error: dict[str, Any]) -> str:
    context = error.get("ctx") or {}
    inner = context.get("error")
    if isinstance(inner, Exception):
        return str(inner)
    message = str(error.get("msg", ""))
    prefix = "Value error, "
    if message.startswith(prefix):
        return message[len(prefix) :]
    return message


def _booking_field_errors(
    exc: ValidationError,
) -> tuple[str | None, str | None]:
    name_error: str | None = None
    contact_error: str | None = None
    for error in exc.errors():
        location = error.get("loc") or ()
        field = location[0] if location else None
        message = _user_facing_error(error)
        if field == "client_name" and name_error is None:
            name_error = message or "Введите имя."
        elif field == "client_contact" and contact_error is None:
            contact_error = message or "Укажите контакт для связи."
    return name_error, contact_error


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    date_query: Annotated[str | None, Query(alias="date")] = None,
    cancelled: str | None = None,
) -> HTMLResponse:
    """Show the public schedule for an optionally selected date."""
    selected_date: datetime.date | None = None
    error_message: str | None = None

    if date_query:
        try:
            selected_date = datetime.date.fromisoformat(date_query)
        except ValueError:
            error_message = "Укажите корректную дату."

    slots = (
        get_slots_by_date(session, selected_date)
        if selected_date is not None
        else []
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "selected_date": selected_date,
            "formatted_date": (
                format_ru_date(selected_date) if selected_date else ""
            ),
            "date_value": date_query or "",
            "slots": slots,
            "error_message": error_message,
            "success_message": (
                "Запись отменена. Время снова доступно для бронирования."
                if cancelled == "1" and error_message is None
                else None
            ),
        },
    )


@router.get("/booking/{slot_id}", response_class=HTMLResponse)
async def booking_form(
    request: Request,
    slot_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Show the booking form for a free slot."""
    slot = get_slot_by_id(session, slot_id)
    if slot is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Слот не найден",
                "message": "Выбранное время не найдено. Вернитесь к расписанию и выберите другой слот.",
                "back_url": "/",
            },
            status_code=404,
        )

    if slot.status != "free":
        return templates.TemplateResponse(
            request=request,
            name="booking.html",
            context={
                "slot": slot,
                "formatted_date": format_ru_date(slot.date),
                "back_url": _schedule_url(slot.date),
                "occupied": True,
                "client_name": "",
                "client_contact": "",
                "name_error": None,
                "contact_error": None,
            },
            status_code=409,
        )

    return templates.TemplateResponse(
        request=request,
        name="booking.html",
        context={
            "slot": slot,
            "formatted_date": format_ru_date(slot.date),
            "back_url": _schedule_url(slot.date),
            "occupied": False,
            "client_name": "",
            "client_contact": "",
            "name_error": None,
            "contact_error": None,
        },
    )


@router.post("/booking/{slot_id}", response_class=HTMLResponse)
async def submit_booking(
    request: Request,
    slot_id: int,
    session: Annotated[Session, Depends(get_db)],
    client_name: Annotated[str, Form()] = "",
    client_contact: Annotated[str, Form()] = "",
) -> Response:
    """Validate the form, create a booking, and redirect to the success page."""
    slot = get_slot_by_id(session, slot_id)
    if slot is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Слот не найден",
                "message": "Выбранное время не найдено. Вернитесь к расписанию и выберите другой слот.",
                "back_url": "/",
            },
            status_code=404,
        )

    try:
        booking_data = BookingCreate.model_validate(
            {
                "client_name": client_name,
                "client_contact": client_contact,
            }
        )
    except ValidationError as exc:
        name_error, contact_error = _booking_field_errors(exc)
        occupied = slot.status != "free"
        return templates.TemplateResponse(
            request=request,
            name="booking.html",
            context={
                "slot": slot,
                "formatted_date": format_ru_date(slot.date),
                "back_url": _schedule_url(slot.date),
                "occupied": occupied,
                "client_name": client_name,
                "client_contact": client_contact,
                "name_error": name_error,
                "contact_error": contact_error,
            },
            status_code=409 if occupied else 400,
        )

    try:
        booking = create_booking(session, slot_id, booking_data)
    except SlotNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Слот не найден",
                "message": "Выбранное время не найдено. Вернитесь к расписанию и выберите другой слот.",
                "back_url": "/",
            },
            status_code=404,
        )
    except (SlotNotAvailableError, BookingConflictError):
        current_slot = get_slot_by_id(session, slot_id)
        return templates.TemplateResponse(
            request=request,
            name="booking.html",
            context={
                "slot": current_slot or slot,
                "formatted_date": format_ru_date(slot.date),
                "back_url": _schedule_url(slot.date),
                "occupied": True,
                "client_name": booking_data.client_name,
                "client_contact": booking_data.client_contact,
                "name_error": None,
                "contact_error": None,
            },
            status_code=409,
        )

    return RedirectResponse(
        url=f"/booking/{booking.id}/success",
        status_code=303,
    )


@router.get("/booking/{booking_id}/success", response_class=HTMLResponse)
async def booking_success(
    request: Request,
    booking_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Show a confirmation page loaded from the saved booking."""
    booking = get_booking_by_id(session, booking_id)
    if booking is None or booking.slot is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Запись не найдена",
                "message": "Подтверждение записи не найдено.",
                "back_url": "/",
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="success.html",
        context={
            "booking": booking,
            "slot": booking.slot,
            "formatted_date": format_ru_date(booking.slot.date),
            "back_url": _schedule_url(booking.slot.date),
            "cancel_url": f"/booking/{booking.id}/cancel",
        },
    )


@router.get("/booking/{booking_id}/cancel", response_class=HTMLResponse)
async def cancel_booking_confirmation(
    request: Request,
    booking_id: int,
    session: Annotated[Session, Depends(get_db)],
    source: str | None = None,
) -> HTMLResponse:
    """Show a confirmation page before cancelling a booking."""
    booking = get_booking_by_id(session, booking_id)
    if booking is None or booking.slot is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Запись не найдена",
                "message": "Запись для отмены не найдена.",
                "back_url": "/",
            },
            status_code=404,
        )

    cancel_source = _normalize_cancel_source(source)
    back_url = (
        "/admin?" + urlencode({"date": booking.slot.date.isoformat()})
        if cancel_source == "admin"
        else _schedule_url(booking.slot.date)
    )

    return templates.TemplateResponse(
        request=request,
        name="cancel.html",
        context={
            "booking": booking,
            "slot": booking.slot,
            "formatted_date": format_ru_date(booking.slot.date),
            "source": cancel_source,
            "back_url": back_url,
        },
    )


@router.post("/booking/{booking_id}/cancel", response_class=HTMLResponse)
async def submit_cancel_booking(
    request: Request,
    booking_id: int,
    session: Annotated[Session, Depends(get_db)],
    source: Annotated[str, Form()] = "public",
) -> Response:
    """Cancel a booking through the service layer and redirect after POST."""
    cancel_source = _normalize_cancel_source(source)

    try:
        slot = cancel_booking(session, booking_id)
    except BookingNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Запись не найдена",
                "message": "Запись для отмены не найдена или уже отменена.",
                "back_url": "/",
            },
            status_code=404,
        )
    except BookingCancellationError:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={
                "page_title": "Отмена не выполнена",
                "message": "Не удалось отменить запись. Попробуйте ещё раз.",
                "back_url": "/",
            },
            status_code=409,
        )

    return RedirectResponse(
        url=_cancel_result_url(slot.date, cancel_source),
        status_code=303,
    )
