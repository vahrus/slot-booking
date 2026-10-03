from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.database.models import Booking, Slot
from app.schemas.booking import BookingCreate
from app.services.slots import get_slot_by_id
from app.timezone import is_past_slot


class SlotNotFoundError(Exception):
    """Raised when the requested slot does not exist."""


class SlotNotAvailableError(Exception):
    """Raised when the slot is no longer free."""


class SlotInPastError(SlotNotAvailableError):
    """Raised when the slot's start time has already been reached."""


class BookingConflictError(Exception):
    """Raised when the database rejects a second booking for the same slot."""


class BookingNotFoundError(Exception):
    """Raised when the requested booking does not exist."""


class BookingCancellationError(Exception):
    """Raised when cancellation could not be committed."""


def slot_booking_state(slot: Slot) -> Literal["available", "booked", "past"]:
    """Share booking eligibility with the public UI, without changing DB status."""
    if slot.status != "free":
        return "booked"
    if is_past_slot(slot.date, slot.time, inclusive=True):
        return "past"
    return "available"


def create_booking(
    session: Session,
    slot_id: int,
    booking_data: BookingCreate,
) -> Booking:
    """Create a booking and mark the slot as booked in one transaction."""
    slot = get_slot_by_id(session, slot_id)
    if slot is None:
        raise SlotNotFoundError
    state = slot_booking_state(slot)
    if state == "booked":
        raise SlotNotAvailableError
    if state == "past":
        raise SlotInPastError

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


def cancel_booking(session: Session, booking_id: int) -> Slot:
    """Delete a booking and mark its slot as free in one transaction."""
    booking = get_booking_by_id(session, booking_id)
    if booking is None or booking.slot is None:
        raise BookingNotFoundError

    slot = booking.slot
    slot.status = "free"
    session.delete(booking)

    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise BookingCancellationError from error

    session.refresh(slot)
    return slot
