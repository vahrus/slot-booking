import datetime

from pydantic import BaseModel, field_validator


class SlotCreate(BaseModel):
    """Validated input for creating a schedule slot."""

    date: datetime.date
    time: datetime.time

    @field_validator("time")
    @classmethod
    def truncate_to_minute(cls, value: datetime.time) -> datetime.time:
        """Store wall-clock minutes so 10:00:30 and 10:00 are one slot."""
        return value.replace(second=0, microsecond=0)
