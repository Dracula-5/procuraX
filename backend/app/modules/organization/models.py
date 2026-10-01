import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPkMixin


class Department(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, TenantMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("org_id", "code"),)

    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(20))
    # users.department_id → departments and departments.head_user_id → users form a cycle;
    # use_alter creates this FK after both tables exist.
    head_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", use_alter=True, name="fk_departments_head_user_id_users")
    )


class CostCenter(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "cost_centers"
    __table_args__ = (UniqueConstraint("org_id", "code"),)

    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Budget(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("org_id", "cost_center_id", "fiscal_year"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    cost_center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(Text)
