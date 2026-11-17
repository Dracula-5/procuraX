"""National Tax Agency qualified-invoice issuer publication (適格請求書発行事業者公表システム) Web-API.

Looks up a registration number through the official Web-API (version 1, `num` endpoint, JSON) and
caches the answer with its fetch time. Needs an application ID that the NTA issues on request
(PROCURAX_NTA_APPLICATION_ID); off by default. The website is never scraped.

Fallbacks, in order: a fresh cached answer; the API; a stale cached answer if the API is unreachable;
otherwise no registry evidence (the invoice is then checked with the check digit and vendor master
only, as before). The source used is recorded on the invoice match.

The field names parsed here follow the NTA Web-API specification (version 1, JSON response
`announcement[]`: registratedNumber, latest, name, registrationDate, disposalDate, expireDate). They are
exercised against a local mock in tests and must be confirmed once an application ID is available.
"""

from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import utcnow
from app.domain.qualified_invoice import RegistryRecord, is_valid_registration_number
from app.integrations.models import QualifiedInvoiceRegistryEntry


class RegistryUnavailableError(RuntimeError):
    pass


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_announcement(number: str, body: dict, fetched_at: datetime) -> tuple[RegistryRecord, dict | None]:
    """Turn a Web-API JSON answer into a record (pure; tested against fixture responses)."""
    announcements = body.get("announcement") or []
    if not isinstance(announcements, list):
        raise RegistryUnavailableError("Unexpected response shape from the NTA Web-API")
    matching = [a for a in announcements if isinstance(a, dict) and a.get("registratedNumber") == number]
    latest = next((a for a in matching if str(a.get("latest")) == "1"), matching[-1] if matching else None)
    if latest is None:
        return RegistryRecord(registration_number=number, found=False, fetched_at=fetched_at), None
    return (
        RegistryRecord(
            registration_number=number,
            found=True,
            fetched_at=fetched_at,
            name=(latest.get("name") or None),
            registered_on=_date(latest.get("registrationDate")),
            revoked_on=_date(latest.get("disposalDate")),
            expired_on=_date(latest.get("expireDate")),
        ),
        latest,
    )


class NtaRegistryClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch(self, number: str) -> tuple[RegistryRecord, dict | None]:
        params = {
            "id": self.settings.nta_application_id or "",
            "number": number,
            "type": "21",
            "history": "0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.nta_timeout_seconds, transport=self.transport, follow_redirects=False
            ) as client:
                response = await client.get(
                    f"{self.settings.nta_api_base_url.rstrip('/')}/num", params=params
                )
        except httpx.HTTPError as exc:
            raise RegistryUnavailableError(f"NTA Web-API unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RegistryUnavailableError(f"NTA Web-API answered HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RegistryUnavailableError("NTA Web-API returned invalid JSON") from exc
        return parse_announcement(number, body, utcnow())


async def lookup(
    session: AsyncSession,
    number: str | None,
    *,
    settings: Settings | None = None,
    client: NtaRegistryClient | None = None,
    now: datetime | None = None,
) -> tuple[RegistryRecord | None, str]:
    """(record or None, source). Sources: disabled, not_applicable, cache, nta_web_api, cache_stale, unavailable."""
    settings = settings or get_settings()
    if not settings.nta_registry_enabled or not settings.nta_application_id:
        return None, "disabled"
    if number is None or not is_valid_registration_number(number):
        return None, "not_applicable"  # local checks reject it anyway; do not spend an API call
    now = now or utcnow()
    cached = await session.get(QualifiedInvoiceRegistryEntry, number)
    if cached is not None and cached.fetched_at >= now - timedelta(hours=settings.nta_cache_hours):
        return cached.to_record(), "cache"
    try:
        record, payload = await (client or NtaRegistryClient(settings)).fetch(number)
    except RegistryUnavailableError:
        if cached is not None:
            return cached.to_record(), "cache_stale"
        return None, "unavailable"
    values = {
        "found": record.found,
        "name": record.name,
        "registered_on": record.registered_on,
        "revoked_on": record.revoked_on,
        "expired_on": record.expired_on,
        "payload": payload,
        "fetched_at": record.fetched_at,
    }
    await session.execute(
        insert(QualifiedInvoiceRegistryEntry)
        .values(registration_number=number, **values)
        .on_conflict_do_update(index_elements=["registration_number"], set_=values)
    )
    return record, "nta_web_api"
