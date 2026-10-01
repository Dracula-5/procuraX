from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Rejected at startup when PROCURAX_ENV=prod (see _prod_guards).
_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"  # noqa: S105


def normalize_database_url(url: str) -> str:
    """Accept a provider's connection string as pasted (e.g. Neon's) for the asyncpg driver.

    Providers hand out `postgres://` or `postgresql://` URLs with libpq options such as
    `sslmode=require&channel_binding=require`; asyncpg needs the `+asyncpg` driver and `ssl=`.
    """
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg" if parts.scheme in {"postgres", "postgresql"} else parts.scheme
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            query.append(("ssl", value))
        elif key != "channel_binding":  # libpq-only; asyncpg negotiates SCRAM itself
            query.append((key, value))
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PROCURAX_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "ProcuraX API"
    api_prefix: str = "/api/v1"

    # Runtime connection: least-privilege role, subject to Row-Level Security.
    database_url: str = "postgresql+asyncpg://procurax_app:procurax_app_dev@127.0.0.1:55433/procurax"
    # Migration / owner connection. Never used by request handlers.
    database_admin_url: str = "postgresql+asyncpg://procurax:procurax_dev@127.0.0.1:55433/procurax"
    # Name of the runtime DB role; migrations grant table privileges to it.
    app_db_role: str = "procurax_app"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    jwt_secret: str = _DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    invitation_ttl_hours: int = 72

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    public_app_url: str = "http://localhost:5173"

    # Enables one-click persona login for the fictional demo organisation only.
    demo_mode: bool = True
    # Public portfolio deployment: allows demo_mode under prod settings, and by default turns
    # off self-service tenant registration so anonymous visitors cannot store their own data.
    public_demo: bool = False
    registration_enabled: bool | None = None  # None = enabled unless public_demo

    @property
    def allows_registration(self) -> bool:
        return self.registration_enabled if self.registration_enabled is not None else not self.public_demo

    # Approval SLA used to compute due dates for approval steps.
    approval_sla_hours: int = 48
    # In-process SLA escalation sweep interval; 0 disables it (run scripts/run_sla_escalations.py
    # from an external scheduler instead). Replicas coordinate through a PostgreSQL advisory lock.
    sla_escalation_interval_seconds: int = Field(default=0, ge=0)

    # Failed-login throttle per client address + e-mail (in-process; one API instance).
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_seconds: int = Field(default=900, ge=1)

    tesseract_path: str | None = None
    pdftoppm_path: str | None = None
    # Directory containing *.traineddata (sets TESSDATA_PREFIX for the OCR subprocess).
    tessdata_path: str | None = None
    ocr_languages: str = "eng"

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _prod_guards(self) -> "Settings":
        # The SPA's own origin is the only browser origin by default, so a deployment sets one URL.
        if "cors_origins" not in self.model_fields_set:
            self.cors_origins = [self.public_app_url.rstrip("/")]
        if self.env == "prod" and self.jwt_secret == _DEV_JWT_SECRET:
            raise ValueError("PROCURAX_JWT_SECRET must be set in production")
        if len(self.jwt_secret) < 32:
            raise ValueError("PROCURAX_JWT_SECRET must be at least 32 characters")
        if self.env == "prod":
            if self.demo_mode and not self.public_demo:
                raise ValueError(
                    "PROCURAX_DEMO_MODE must be false in production unless PROCURAX_PUBLIC_DEMO=true"
                )
            if self.db_echo:
                raise ValueError("PROCURAX_DB_ECHO must be false in production")
            if not self.public_app_url.startswith("https://"):
                raise ValueError("PROCURAX_PUBLIC_APP_URL must use HTTPS in production")
            if not self.cors_origins or any(
                origin == "*" or not origin.startswith("https://") for origin in self.cors_origins
            ):
                raise ValueError("Production CORS origins must be explicit HTTPS origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
