import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter


def test_production_settings_require_safe_demo_database_and_https_configuration() -> None:
    secure = {
        "env": "prod",
        "jwt_secret": "s" * 64,
        "demo_mode": False,
        "db_echo": False,
        "public_app_url": "https://app.example.com",
        "cors_origins": ["https://app.example.com"],
    }
    Settings(**secure)

    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings(**{**secure, "demo_mode": True})
    with pytest.raises(ValidationError, match="HTTPS origins"):
        Settings(**{**secure, "cors_origins": ["*"]})
    with pytest.raises(ValidationError, match="DB_ECHO"):
        Settings(**{**secure, "db_echo": True})


def test_json_logging_redacts_sensitive_extra_fields_recursively() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "auth event", (), None)
    record.extra = {
        "access_token": "secret-value",
        "nested": {"api_key": "another-secret", "safe_count": 2},
    }

    payload = json.loads(JsonFormatter().format(record))

    assert payload["extra"]["access_token"] == "[REDACTED]"
    assert payload["extra"]["nested"]["api_key"] == "[REDACTED]"
    assert payload["extra"]["nested"]["safe_count"] == 2


def test_public_demo_allows_demo_mode_in_prod_and_disables_registration() -> None:
    from app.core.config import Settings

    base = {
        "env": "prod",
        "jwt_secret": "x" * 40,
        "public_app_url": "https://procurax.example",
        "cors_origins": ["https://procurax.example"],
        "demo_mode": True,
        "database_url": "postgresql+asyncpg://app:pw@db.example/procurax",
    }
    with pytest.raises(ValueError, match="PROCURAX_PUBLIC_DEMO"):
        Settings(**base)
    public = Settings(**base, public_demo=True)
    assert public.allows_registration is False
    assert Settings(**base, public_demo=True, registration_enabled=True).allows_registration is True


def test_provider_database_urls_are_normalized_for_asyncpg() -> None:
    from app.core.config import normalize_database_url

    neon = "postgresql://app:p%40ss@ep-x.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert normalize_database_url(neon) == (
        "postgresql+asyncpg://app:p%40ss@ep-x.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )
    assert normalize_database_url("postgres://u:p@h:5432/db").startswith("postgresql+asyncpg://")
    local = "postgresql+asyncpg://u:p@127.0.0.1:55433/db"
    assert normalize_database_url(local) == local


def test_cors_defaults_to_the_public_app_origin() -> None:
    from app.core.config import Settings

    prod = Settings(
        env="prod",
        jwt_secret="x" * 40,
        demo_mode=False,
        public_app_url="https://web.example/",
        database_url="postgresql+asyncpg://app:pw@db.example/procurax",
    )
    assert prod.cors_origins == ["https://web.example"]
    explicit = Settings(public_app_url="http://localhost:5173", cors_origins=["http://localhost:3000"])
    assert explicit.cors_origins == ["http://localhost:3000"]


def test_bare_production_env_lists_every_missing_setting_at_once(monkeypatch) -> None:
    import os

    from app.core.config import Settings

    for key in [k for k in os.environ if k.startswith("PROCURAX_")]:
        monkeypatch.delenv(key)
    with pytest.raises(ValueError) as error:
        Settings(env="prod", _env_file=None)  # a host where only PROCURAX_ENV was configured
    message = str(error.value)
    for setting in (
        "PROCURAX_JWT_SECRET",
        "PROCURAX_DATABASE_URL",
        "PROCURAX_DEMO_MODE",
        "PROCURAX_PUBLIC_APP_URL",
    ):
        assert setting in message
