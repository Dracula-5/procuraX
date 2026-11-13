import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPkMixin, utcnow
from app.domain.catalog import Locale


class Organization(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),)

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="JPY")
    country: Mapped[str] = mapped_column(String(2), default="JP")
    # Japanese companies commonly run April–March fiscal years.
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, default=4)
    # Business dates (fiscal year of a submission, SLA display) use the tenant's local time.
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Tokyo")
    # Fictional demo tenant: persona login enabled, excluded from genuine-usage metrics.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # Payer details for Zengin transfer files (consignor code, name and debit account).
    payment_settings: Mapped[dict | None] = mapped_column(JSONB)
    # Control accounts for the ERP journal export (payable, input tax, bank, default expense).
    ledger_settings: Mapped[dict | None] = mapped_column(JSONB)


class User(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, TenantMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(f"locale IN ({', '.join(repr(v.value) for v in Locale)})", name="valid_locale"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(120))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # UI language chosen by the user; NULL means "not chosen" (the browser language applies).
    locale: Mapped[str | None] = mapped_column(String(5))
    # TOTP (RFC 6238). Secrets are encrypted with the data key; recovery codes are stored as HMACs.
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    mfa_pending_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_last_step: Mapped[int | None] = mapped_column(BigInteger)  # last accepted time step (no replay)
    mfa_recovery_hashes: Mapped[list[str] | None] = mapped_column(JSONB)


class RoleDef(Base):
    """Seeded from app.domain.rbac (code is the source of truth)."""

    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)


class PermissionDef(Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    description: Mapped[str] = mapped_column(Text)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_key: Mapped[str] = mapped_column(ForeignKey("roles.key", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permissions.key", ondelete="CASCADE"), primary_key=True
    )


class UserRole(TenantMixin, Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("roles.key"), primary_key=True)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invitation(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role_keys: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    job_title: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrgSequence(Base):
    """Per-tenant human-readable numbering (PR-2026-000001), allocated under a row lock."""

    __tablename__ = "org_sequences"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, default=1)


class AuthSession(UUIDPkMixin, TenantMixin, Base):
    """One refresh token of a sign-in session family (ADR-019). Only the token's SHA-256 is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_active", "org_id", "user_id", "revoked_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # password, password_mfa, recovery_code, oidc, register, invitation, demo
    method: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    family_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(40))
    user_agent: Mapped[str | None] = mapped_column(String(200))


class AuthThrottle(Base):
    """Failed-attempt counters shared by every API process. Not tenant data: keys are SHA-256 hashes of
    "purpose:address:account" strings, so no e-mail or client address is stored in clear."""

    __tablename__ = "auth_throttle"
    __table_args__ = (PrimaryKeyConstraint("key_hash", "bucket"),)

    key_hash: Mapped[str] = mapped_column(String(64))
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
