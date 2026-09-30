import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import Slot
from app.schemas.slot import SlotCreate


class SlotAlreadyExistsError(Exception):
    """Raised when a slot already exists at the requested date and time."""


def create_slot(session: Session, slot_data: SlotCreate) -> Slot:
    """Create and persist a free slot."""
    slot = Slot(
        date=slot_data.date,
        time=slot_data.time,
        status="free",
    )
    session.add(slot)

    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise SlotAlreadyExistsError from error

    session.refresh(slot)
    return slot


def get_slot_by_id(session: Session, slot_id: int) -> Slot | None:
    """Return one slot by identifier, or None if it does not exist."""
    return session.get(Slot, slot_id)


def get_slots_by_date(
    session: Session,
    selected_date: datetime.date,
) -> list[Slot]:
    """Return slots for one date ordered by time."""
    statement = (
        select(Slot)
        .where(Slot.date == selected_date)
        .order_by(Slot.time.asc())
    )
    return list(session.scalars(statement).all())

