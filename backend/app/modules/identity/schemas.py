import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.catalog import Locale
from app.domain.rbac import Role

_PASSWORD_HINT = "at least 10 characters, including a letter and a digit"  # noqa: S105


def _check_password(value: str) -> str:
    if len(value) < 10 or not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError(f"Password must be {_PASSWORD_HINT}")
    return value


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    base_currency: str
    country: str
    fiscal_year_start_month: int
    timezone: str
    is_demo: bool
    created_at: datetime


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(max_length=128)
    base_currency: str = Field(default="JPY", pattern=r"^[A-Z]{3}$")
    country: str = Field(default="JP", pattern=r"^[A-Z]{2}$")
    fiscal_year_start_month: int = Field(default=4, ge=1, le=12)

    _pw = field_validator("password")(_check_password)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=128)

    _pw = field_validator("password")(_check_password)


class DemoLoginRequest(BaseModel):
    email: EmailStr


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    job_title: str | None
    department_id: uuid.UUID | None
    manager_id: uuid.UUID | None
    roles: list[str]
    permissions: list[str]
    organization: OrgOut
    locale: Locale | None = None
    mfa_enabled: bool = False


class PreferencesUpdate(BaseModel):
    """The caller's own settings. `locale: null` clears the choice (the browser language applies)."""

    model_config = ConfigDict(extra="forbid")

    locale: Locale | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    # Sent back as X-CSRF-Token on /auth/refresh and /auth/logout (the refresh token is in a cookie).
    csrf_token: str | None = None
    user: MeOut


class MfaChallenge(BaseModel):
    """The password was right; the second step (POST /auth/mfa/verify) needs a code."""

    mfa_required: bool = True
    mfa_token: str
    expires_in: int


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=2000)
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=40)


class MfaCodeIn(BaseModel):
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=40)


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    digits: int
    period: int


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class SessionOut(BaseModel):
    id: uuid.UUID  # the session family
    method: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    user_agent: str | None
    current: bool


class AuthConfigOut(BaseModel):
    registration_enabled: bool
    demo_mode: bool
    oidc_enabled: bool
    oidc_display_name: str | None
    mfa_available: bool


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    job_title: str | None
    department_id: uuid.UUID | None
    manager_id: uuid.UUID | None
    roles: list[str]
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    mfa_enabled: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    department_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    roles: list[Role] | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    roles: list[Role] = Field(min_length=1)
    department_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    job_title: str | None = Field(default=None, max_length=120)


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    roles: list[str]
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    # Returned once, at creation. Only a hash of the token is stored.
    invite_url: str | None = None


class DemoPersonaOut(BaseModel):
    email: str
    full_name: str
    job_title: str | None
    roles: list[str]


class RoleOut(BaseModel):
    key: str
    name: str
    description: str
    permissions: list[str]
    assignable: bool
