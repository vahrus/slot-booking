from pydantic import BaseModel, field_validator


NAME_MAX_LENGTH = 100
CONTACT_MAX_LENGTH = 100


class BookingCreate(BaseModel):
    """Validated client details for booking a free slot."""

    client_name: str
    client_contact: str

    @field_validator("client_name", "client_contact", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("client_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Введите имя.")
        if len(value) > NAME_MAX_LENGTH:
            raise ValueError("Имя слишком длинное.")
        return value

    @field_validator("client_contact")
    @classmethod
    def validate_contact(cls, value: str) -> str:
        if not value:
            raise ValueError("Укажите контакт для связи.")
        if len(value) > CONTACT_MAX_LENGTH:
            raise ValueError("Контакт слишком длинный.")
        return value
