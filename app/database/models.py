from __future__ import annotations

import datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.database import Base


class Slot(Base):
    """A date and time that can receive at most one booking."""

    __tablename__ = "slots"
    __table_args__ = (
        UniqueConstraint("date", "time", name="uq_slots_date_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    time: Mapped[datetime.time] = mapped_column(Time, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="free",
        server_default="free",
    )

    booking: Mapped[Booking | None] = relationship(
        back_populates="slot",
        uselist=False,
    )


class Booking(Base):
    """Client details associated with exactly one slot."""

    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("slot_id", name="uq_bookings_slot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("slots.id"),
        nullable=False,
    )
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_contact: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )

    slot: Mapped[Slot] = relationship(back_populates="booking")
