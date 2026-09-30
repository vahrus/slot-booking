import datetime

from pydantic import BaseModel


class SlotCreate(BaseModel):
    """Validated input for creating a schedule slot."""

    date: datetime.date
    time: datetime.time
