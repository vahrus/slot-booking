import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.calendar_view import booking_calendar
from app.database.database import get_db
from app.routers.public import format_ru_date
from app.schemas.slot import SlotCreate
from app.services.slots import (
    SlotAlreadyExistsError,
    create_slot,
    get_slots_by_date,
)
from app.static_assets import stylesheet_version
from app.timezone import local_today


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["stylesheet_version"] = stylesheet_version
templates.env.globals["booking_calendar"] = booking_calendar


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None

    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _admin_context(
    session: Session,
    selected_date: datetime.date | None,
    *,
    date_value: str = "",
    time_value: str = "",
    success_message: str | None = None,
    error_message: str | None = None,
    date_error: str | None = None,
    time_error: str | None = None,
    filter_error: bool = False,
) -> dict[str, Any]:
    slots = (
        get_slots_by_date(session, selected_date)
        if selected_date is not None
        else []
    )
    return {
        "selected_date": selected_date,
        "formatted_date": format_ru_date(selected_date) if selected_date else "",
        "date_value": date_value,
        "time_value": time_value,
        "slots": slots,
        "success_message": success_message,
        "error_message": error_message,
        "date_error": date_error,
        "time_error": time_error,
        "filter_error": filter_error,
        "summary": {
            "total": len(slots),
            "free": sum(slot.status == "free" for slot in slots),
            "booked": sum(slot.status == "booked" for slot in slots),
        },
    }


@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    date_query: Annotated[str | None, Query(alias="date")] = None,
    created: str | None = None,
    created_time: Annotated[str | None, Query(alias="time")] = None,
    cancelled: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Show slot creation controls and the schedule for one date."""
    selected_date = _parse_date(date_query) if date_query else local_today()
    error_message: str | None = None
    success_message: str | None = None

    if date_query and selected_date is None:
        error_message = "Укажите корректную дату расписания."
    elif error == "duplicate":
        error_message = "Слот на выбранные дату и время уже существует."
    elif created == "1" and selected_date is not None and created_time:
        try:
            parsed_time = datetime.time.fromisoformat(created_time)
        except ValueError:
            parsed_time = None
        if parsed_time is not None:
            success_message = (
                f"Слот {parsed_time.strftime('%H:%M')} на "
                f"{selected_date.strftime('%d.%m.%Y')} создан."
            )
    elif cancelled == "1" and selected_date is not None:
        success_message = (
            "Запись отменена. Проверьте доступное время в расписании."
        )

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_admin_context(
            session,
            selected_date,
            date_value=date_query or selected_date.isoformat(),
            success_message=success_message,
            error_message=error_message,
            filter_error=bool(date_query and selected_date is None),
        ),
    )


@router.post("/slots", response_class=HTMLResponse)
async def add_slot(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    date_value: Annotated[str, Form(alias="date")] = "",
    time_value: Annotated[str, Form(alias="time")] = "",
) -> Response:
    """Validate a form submission, create a slot, and redirect."""
    try:
        slot_data = SlotCreate.model_validate(
            {"date": date_value, "time": time_value}
        )
    except ValidationError as exc:
        selected_date = _parse_date(date_value)
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context=_admin_context(
                session,
                selected_date,
                date_value=date_value,
                time_value=time_value,
                error_message="Укажите корректные дату и время.",
                date_error=(
                    "Укажите корректную дату."
                    if any(error["loc"] == ("date",) for error in exc.errors())
                    else None
                ),
                time_error=(
                    "Укажите корректное время."
                    if any(error["loc"] == ("time",) for error in exc.errors())
                    else None
                ),
            ),
            status_code=400,
        )

    try:
        slot = create_slot(session, slot_data)
    except SlotAlreadyExistsError:
        query = urlencode(
            {"date": slot_data.date.isoformat(), "error": "duplicate"}
        )
        return RedirectResponse(url=f"/admin?{query}", status_code=303)

    query = urlencode(
        {
            "date": slot.date.isoformat(),
            "created": "1",
            "time": slot.time.strftime("%H:%M"),
        }
    )
    return RedirectResponse(url=f"/admin?{query}", status_code=303)
