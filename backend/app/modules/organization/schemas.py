import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    head_user_id: uuid.UUID | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    head_user_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    head_user_id: uuid.UUID | None
    head_name: str | None = None
    created_at: datetime


class CostCenterCreate(BaseModel):
    department_id: uuid.UUID
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)


class CostCenterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class CostCenterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    department_id: uuid.UUID
    code: str
    name: str
    is_active: bool


class BudgetCreate(BaseModel):
    cost_center_id: uuid.UUID
    fiscal_year: int = Field(ge=2000, le=2100)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)


class BudgetUpdate(BaseModel):
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)


class BudgetOut(BaseModel):
    id: uuid.UUID
    cost_center_id: uuid.UUID
    cost_center_code: str
    cost_center_name: str
    department_id: uuid.UUID
    fiscal_year: int
    amount: Decimal
    currency: str
    notes: str | None
    # Derived live from purchase requests (not stored), so it can never drift.
    committed: Decimal
    pending: Decimal
    available: Decimal
    utilization: Decimal | None
