import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.slots import get_slots_by_date


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


def format_ru_date(value: datetime.date) -> str:
    """Format a date as a readable Russian phrase."""
    return f"{value.day} {_RU_MONTHS[value.month - 1]} {value.year}"


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    date_query: Annotated[str | None, Query(alias="date")] = None,
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
        },
    )
