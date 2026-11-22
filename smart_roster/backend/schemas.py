import re
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, field_validator

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=120)
    employee_id: str = Field(min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=100)
    pod: str = "General"
    max_days: int = Field(default=6, ge=1, le=14)
    max_work_days: int = Field(default=6, ge=1, le=7)
    min_offs: int = Field(default=1, ge=0, le=7)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("A valid email address is required.")
        return value

    @field_validator("name", "employee_id", "pod")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

class EmployeePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, min_length=3, max_length=120)
    employee_id: str | None = Field(default=None, min_length=1, max_length=50)
    title: str | None = Field(default=None, max_length=100)
    pod: str | None = None
    max_days: int | None = Field(default=None, ge=1, le=14)
    max_work_days: int | None = Field(default=None, ge=1, le=7)
    min_offs: int | None = Field(default=None, ge=0, le=7)
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("A valid email address is required.")
        return value

    @field_validator("name", "employee_id", "pod")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank.")
        return value

class BrandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

class ProcessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

class AllocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: int
    brand_id: int
    process_id: int
    role: str = "PRIMARY"

class CoverageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_id: int
    process_id: int
    min_staff: int = Field(default=1, ge=1, le=20)
    calendar: str = "DAILY"
    work_days: str = "0,1,2,3,4,5"

class TimeOffIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: int
    day: date
    kind: str

class HolidayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    description: str = Field(min_length=1, max_length=200)
    full_closure: bool = True

class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    time_limit: int = Field(default=10, ge=1, le=60)
    allow_incomplete: bool = False

class PublishIn(BaseModel):
    roster_id: int

class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: int | None = None
    brand_id: int | None = None
    process_id: int | None = None
    day: date | None = None
    role: str | None = None

class BackupAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: int
    brand_id: int
    process_id: int
    day: date
