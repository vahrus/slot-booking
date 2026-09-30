from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database.models import Booking
from app.schemas.booking import BookingCreate
from app.services.slots import get_slot_by_id


class SlotNotFoundError(Exception):
    """Raised when the requested slot does not exist."""


class SlotNotAvailableError(Exception):
    """Raised when the slot is no longer free."""


class BookingConflictError(Exception):
    """Raised when the database rejects a second booking for the same slot."""


def create_booking(
    session: Session,
    slot_id: int,
    booking_data: BookingCreate,
) -> Booking:
    """Create a booking and mark the slot as booked in one transaction."""
    slot = get_slot_by_id(session, slot_id)
    if slot is None:
        raise SlotNotFoundError
    if slot.status != "free":
        raise SlotNotAvailableError

    booking = Booking(
        slot_id=slot.id,
        client_name=booking_data.client_name,
        client_contact=booking_data.client_contact,
    )
    slot.status = "booked"
    session.add(booking)

    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise BookingConflictError from error

    session.refresh(booking)
    return booking


def get_booking_by_id(session: Session, booking_id: int) -> Booking | None:
    """Return a booking together with its slot, or None if missing."""
    statement = (
        select(Booking)
        .options(joinedload(Booking.slot))
        .where(Booking.id == booking_id)
    )
    return session.scalar(statement)
