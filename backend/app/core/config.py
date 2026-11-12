from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Rejected at startup when PROCURAX_ENV=prod (see _prod_guards).
_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"  # noqa: S105
# Secret for encrypting values stored in the database; rejected in prod when webhooks are on.
_DEV_DATA_KEY = "dev-only-data-encryption-secret-change-me-0123"  # noqa: S105


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

    # --- Notifications (transactional outbox + worker). External channels are off by default. ---
    # Outbox worker inside the API process (seconds; 0 = off, run `python -m app.worker` instead).
    # Several API replicas and workers may run at once: rows are claimed with FOR UPDATE SKIP LOCKED.
    notification_worker_interval_seconds: int = Field(default=0, ge=0)
    notification_batch_size: int = Field(default=50, ge=1, le=1000)
    # Failed deliveries are retried with exponential backoff, then dead-lettered for review.
    notification_max_attempts: int = Field(default=6, ge=1, le=50)
    email_enabled: bool = False
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025  # Mailpit (docker-compose) in development
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    smtp_timeout_seconds: float = Field(default=10, gt=0)
    email_from: str = "ProcuraX <no-reply@procurax.local>"
    # Slack / Microsoft Teams incoming webhooks configured per tenant.
    webhooks_enabled: bool = False
    # Hosts a tenant may point a webhook at: exact host, or ".suffix" for subdomains (blocks SSRF).
    webhook_allowed_hosts: list[str] = Field(
        default_factory=lambda: [
            "hooks.slack.com",
            ".webhook.office.com",
            ".logic.azure.com",
            ".environment.api.powerplatform.com",
        ]
    )
    webhook_timeout_seconds: float = Field(default=10, gt=0)
    # Encrypts secrets kept in the database (tenant webhook URLs): any random string of 32+
    # characters (a Fernet key is derived from it). Comma-separated secrets rotate: the first
    # encrypts, all decrypt. Generate with: python -c "import secrets;print(secrets.token_urlsafe(48))"
    data_encryption_key: str = _DEV_DATA_KEY

    # --- Invoice files ----------------------------------------------------------------------------
    # "local" keeps files on disk (development; a container's disk is not durable), "s3" uses any
    # S3-compatible store: MinIO locally and in CI, AWS S3 or Cloudflare R2 in production.
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: str = "var/documents"
    s3_endpoint_url: str | None = None  # e.g. http://127.0.0.1:9000 for MinIO; None = AWS
    s3_bucket: str = "procurax-documents"
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # Downloads redirect to a short-lived presigned URL (after the API's permission check).
    s3_presign_downloads: bool = True
    s3_presign_seconds: int = Field(default=60, ge=10, le=3600)
    # Invoices must be kept 7 years under Japanese corporate tax law (longer with loss carry-forward);
    # every stored file records its retain-until date. Nothing deletes files automatically.
    document_retention_years: int = Field(default=7, ge=1, le=30)
    # Optional ClamAV (clamd) scan run by the worker. Off: files are marked not_scanned.
    clamav_enabled: bool = False
    clamav_host: str = "127.0.0.1"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = Field(default=30, gt=0)
    # When scanning is off, may invoices with not_scanned files be paid? (Infected/pending never.)
    allow_unscanned_documents: bool = False

    # --- NTA qualified-invoice issuer registry (適格請求書発行事業者公表システム Web-API) ---------
    # Needs an application ID issued by the National Tax Agency. Off: invoices are checked with the
    # registration number's check digit and the vendor master only (the previous behaviour).
    nta_registry_enabled: bool = False
    nta_application_id: str | None = None
    nta_api_base_url: str = "https://web-api.invoice-kohyo.nta.go.jp/1"
    nta_cache_hours: int = Field(default=24, ge=1, le=720)
    nta_timeout_seconds: float = Field(default=5, gt=0, le=30)

    tesseract_path: str | None = None
    pdftoppm_path: str | None = None
    # Directory containing *.traineddata (sets TESSDATA_PREFIX for the OCR subprocess).
    tessdata_path: str | None = None
    ocr_languages: str = "eng"

    @field_validator("public_app_url")
    @classmethod
    def _clean_public_url(cls, value: str) -> str:
        # Tolerate common dashboard paste mistakes: surrounding spaces/quotes and a trailing slash.
        return value.strip().strip("'\"").strip().rstrip("/")

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _prod_guards(self) -> "Settings":
        # The SPA's own origin is the only browser origin by default, so a deployment sets one URL.
        if "cors_origins" not in self.model_fields_set:
            self.cors_origins = [self.public_app_url.rstrip("/")]
        if len(self.jwt_secret) < 32:
            raise ValueError("PROCURAX_JWT_SECRET must be at least 32 characters")
        if any(len(k.strip()) < 32 for k in self.data_encryption_key.split(",")):
            raise ValueError("PROCURAX_DATA_ENCRYPTION_KEY must be 32+ characters (each, if several)")
        if self.env != "prod":
            return self
        # Report every missing or unsafe production setting at once, so a deployment is fixed in
        # one pass instead of one crash per variable.
        problems = []
        if self.jwt_secret == _DEV_JWT_SECRET:
            problems.append("PROCURAX_JWT_SECRET must be set in production (32+ random characters)")
        if self.webhooks_enabled and _DEV_DATA_KEY in self.data_encryption_key.split(","):
            problems.append(
                "PROCURAX_DATA_ENCRYPTION_KEY must be set in production when webhooks are enabled "
                "(32+ random characters)"
            )
        if "database_url" not in self.model_fields_set:
            problems.append(
                "PROCURAX_DATABASE_URL must be set in production (runtime role connection string)"
            )
        if self.demo_mode and not self.public_demo:
            problems.append("PROCURAX_DEMO_MODE must be false in production unless PROCURAX_PUBLIC_DEMO=true")
        if self.db_echo:
            problems.append("PROCURAX_DB_ECHO must be false in production")
        if self.nta_registry_enabled and not self.nta_application_id:
            problems.append("PROCURAX_NTA_APPLICATION_ID is required when PROCURAX_NTA_REGISTRY_ENABLED=true")
        if not self.public_app_url.startswith("https://"):
            source = (
                "not set" if "public_app_url" not in self.model_fields_set else f"got {self.public_app_url!r}"
            )
            problems.append(
                f"PROCURAX_PUBLIC_APP_URL must be the web app's https:// URL in production ({source})"
            )
        elif not self.cors_origins or any(
            origin == "*" or not origin.startswith("https://") for origin in self.cors_origins
        ):
            problems.append("Production CORS origins must be explicit HTTPS origins")
        if problems:
            raise ValueError("Production settings incomplete:\n- " + "\n- ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
