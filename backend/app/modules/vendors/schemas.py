import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.domain.catalog import ContractStatus, SpendCategory, VendorRiskLevel, VendorStatus


class VendorBase(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    corporate_number: str | None = Field(default=None, pattern=r"^\d{13}$")
    invoice_registration_number: str | None = Field(default=None, pattern=r"^T\d{13}$")
    risk_level: VendorRiskLevel = VendorRiskLevel.UNKNOWN
    contract_status: ContractStatus = ContractStatus.NONE
    contract_expires_on: date | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    country: str = Field(default="JP", pattern=r"^[A-Z]{2}$")
    currency: str = Field(default="JPY", pattern=r"^[A-Z]{3}$")
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    website: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)


class VendorCreate(VendorBase):
    name: str = Field(min_length=1, max_length=200)
    categories: list[SpendCategory] = Field(min_length=1)


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    corporate_number: str | None = Field(default=None, pattern=r"^\d{13}$")
    invoice_registration_number: str | None = Field(default=None, pattern=r"^T\d{13}$")
    categories: list[SpendCategory] | None = Field(default=None, min_length=1)
    risk_level: VendorRiskLevel | None = None
    contract_status: ContractStatus | None = None
    contract_expires_on: date | None = None
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    website: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)


class VendorStatusChange(BaseModel):
    status: VendorStatus
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reason_required_for_restrictions(self) -> "VendorStatusChange":
        if self.status in (VendorStatus.SUSPENDED, VendorStatus.BLOCKED) and not (self.reason or "").strip():
            raise ValueError("A reason is required to suspend or block a vendor")
        return self


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    legal_name: str | None
    corporate_number: str | None
    invoice_registration_number: str | None
    categories: list[str]
    status: str
    risk_level: str
    contract_status: str
    contract_expires_on: date | None
    rating: Decimal | None
    contact_name: str | None
    contact_email: str | None
    phone: str | None
    country: str
    currency: str
    payment_terms_days: int
    website: str | None
    notes: str | None
    status_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VendorPage(BaseModel):
    items: list[VendorOut]
    total: int
