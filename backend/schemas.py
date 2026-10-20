"""Input validation and the public API contract for weeks 1 to 3."""
from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.models import OrderStatus

Name = Annotated[str, Field(min_length=1, max_length=255)]
Identifier = Annotated[int, Field(gt=0)]

# Units are normalized, not converted: 1000 g remains 1000 g, not 1 kg.
UNIT_ALIASES = {
    "kg": "kg", "kilogram": "kg", "kilogramy": "kg", "kilogramów": "kg",
    "g": "g", "gram": "g", "gramy": "g", "gramów": "g",
    "t": "t", "tona": "t", "tony": "t",
    "m": "m", "metr": "m", "metry": "m", "mb": "m",
    "cm": "cm", "mm": "mm", "m2": "m2", "m3": "m3",
    "l": "l", "litr": "l", "litry": "l", "ml": "ml",
    "szt": "szt", "sztuka": "szt", "sztuki": "szt", "sztuk": "szt", "pcs": "szt",
}
MATERIAL_UNITS = sorted(set(UNIT_ALIASES.values()))


def normalize_unit(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Jednostka musi być tekstem")
    key = value.strip().casefold().replace("²", "2").replace("³", "3").rstrip(".")
    if not key:
        return None
    if key not in UNIT_ALIASES:
        raise ValueError("Dozwolone jednostki: " + ", ".join(MATERIAL_UNITS))
    return UNIT_ALIASES[key]


def utc_naive(value):
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)

    @field_validator("operator", "completed_by", "production_notes", "material_used", "material",
                     mode="before", check_fields=False)
    @classmethod
    def optional_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class MaterialAmount(InputModel):
    material_quantity: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    material_unit: Optional[str] = None

    @field_validator("material_quantity", mode="before")
    @classmethod
    def normalize_quantity(cls, value):
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            return value or None
        if isinstance(value, bool):
            raise ValueError("Ilość materiału musi być liczbą")
        return value

    @field_validator("material_unit", mode="before")
    @classmethod
    def normalized_unit(cls, value):
        return normalize_unit(value)

    @model_validator(mode="after")
    def quantity_and_unit(self):
        if (self.material_quantity is None) != (self.material_unit is None):
            raise ValueError("Ilość materiału i jednostkę należy podać razem")
        return self


class MaterialRecord(MaterialAmount):
    material_used: Optional[str] = Field(default=None, max_length=255)


class PartMaterialUsage(MaterialAmount):
    pass


class StepCreate(MaterialRecord):
    step_name: Name
    operator: Optional[str] = Field(default=None, max_length=255)
    is_completed: bool = False
    completed_by: Optional[str] = Field(default=None, max_length=255)
    completed_at: Optional[datetime] = None
    production_notes: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("completed_at")
    @classmethod
    def normalize_completion_date(cls, value):
        return utc_naive(value)


class StepUpdate(StepCreate):
    id: Optional[Identifier] = None


class StepProductionRecord(MaterialRecord):
    is_completed: bool
    completed_by: Optional[str] = Field(default=None, max_length=255)
    production_notes: Optional[str] = Field(default=None, max_length=5000)


class StepCompletion(InputModel):
    is_completed: bool


class PartCreate(MaterialAmount):
    part_name: Name
    material: Optional[str] = Field(default=None, max_length=255)
    operator: Optional[str] = Field(default=None, max_length=255)
    client_key: Optional[str] = Field(default=None, max_length=100)
    steps: list[StepCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def quantity_requires_material(self):
        if self.material_quantity is not None and not self.material:
            raise ValueError("Podaj materiał części z zapisaną ilością")
        return self


class PartUpdate(PartCreate):
    id: Optional[Identifier] = None
    steps: list[StepUpdate] = Field(min_length=1)


class OrderCreate(InputModel):
    order_number: Annotated[str, Field(min_length=1, max_length=100)]
    client_name: Name
    date_received: Optional[datetime] = None
    date_deadline: datetime
    operator: Optional[str] = Field(default=None, max_length=255)
    parts: list[PartCreate] = Field(min_length=1)

    @field_validator("date_received", "date_deadline")
    @classmethod
    def normalize_date(cls, value):
        return utc_naive(value)

    @model_validator(mode="after")
    def validate_dates_and_keys(self):
        self.date_received = self.date_received or datetime.utcnow()
        if self.date_deadline.date() < self.date_received.date():
            raise ValueError("Termin realizacji nie może być wcześniejszy niż data otrzymania")
        keys = [part.client_key for part in self.parts if part.client_key is not None]
        if len(keys) != len(set(keys)):
            raise ValueError("Identyfikatory formularza części muszą być unikalne")
        return self


class OrderUpdate(InputModel):
    order_number: Annotated[str, Field(min_length=1, max_length=100)]
    client_name: Name
    date_deadline: datetime
    operator: Optional[str] = Field(default=None, max_length=255)
    parts: list[PartUpdate] = Field(min_length=1)

    @field_validator("date_deadline")
    @classmethod
    def normalize_date(cls, value):
        return utc_naive(value)

    @model_validator(mode="after")
    def unique_identifiers(self):
        ids = [part.id for part in self.parts if part.id is not None]
        keys = [part.client_key for part in self.parts if part.client_key is not None]
        step_ids = [step.id for part in self.parts for step in part.steps if step.id is not None]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)) or len(step_ids) != len(set(step_ids)):
            raise ValueError("Identyfikatory części i operacji nie mogą się powtarzać")
        return self


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PDFResponse(ORMResponse):
    id: int
    file_name: str
    url: str


class StepResponse(ORMResponse):
    id: int
    step_name: str
    position: int = 0
    operator: Optional[str] = None
    is_completed: bool = False
    completed_by: Optional[str] = None
    completed_at: Optional[datetime] = None
    production_notes: Optional[str] = None
    material_used: Optional[str] = None
    material_quantity: Optional[float] = None
    material_unit: Optional[str] = None


class PartResponse(ORMResponse):
    id: int
    part_name: str
    position: int = 0
    material: Optional[str] = None
    material_missing: bool = False
    material_quantity: Optional[float] = None
    material_unit: Optional[str] = None
    operator: Optional[str] = None
    steps: list[StepResponse] = Field(default_factory=list)
    pdfs: list[PDFResponse] = Field(default_factory=list)


class OrderResponse(ORMResponse):
    id: int
    order_number: str
    client_name: str
    status: OrderStatus
    is_approved: bool = False
    needs_correction: bool = False
    date_received: Optional[datetime] = None
    date_deadline: Optional[datetime] = None
    status_changed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    operator: Optional[str] = None
    parts: list[PartResponse]


class StatusEventResponse(ORMResponse):
    id: int
    from_status: Optional[OrderStatus] = None
    to_status: OrderStatus
    occurred_at: datetime
    reason: str


class LoginRequest(InputModel):
    # Password whitespace is significant; normalize the login separately.
    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid", allow_inf_nan=False)
    username: Annotated[str, Field(min_length=1, max_length=100)]
    password: Annotated[str, Field(min_length=1)]

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("password", mode="before")
    @classmethod
    def password_bytes(cls, value):
        if isinstance(value, str) and len(value.encode("utf-8")) > 72:
            raise ValueError("Hasło może mieć maksymalnie 72 bajty UTF-8")
        return value


class RegisterRequest(LoginRequest):
    full_name: Name

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, value):
        return value.strip() if isinstance(value, str) else value
