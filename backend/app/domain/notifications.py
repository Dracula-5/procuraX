"""Notification rules: which events notify whom, on which channels, how retries back off, and the
message text in each language. Pure functions, no I/O; the outbox worker supplies the facts.

Delivery is at-least-once. Every delivery has a deterministic key (event + channel + recipient), used
as a unique constraint for in-app rows and as an idempotency key that receivers can deduplicate on.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from urllib.parse import urlsplit


class EventType(StrEnum):
    APPROVAL_ROUTED = "approval.routed"
    APPROVAL_ESCALATED = "approval.escalated"
    APPROVAL_DELEGATED = "approval.delegated"
    REQUEST_REJECTED = "purchase_request.rejected"
    REQUEST_APPROVED = "purchase_request.approved"
    PO_SENT = "purchase_order.sent"
    INVOICE_MISMATCH = "invoice.mismatch"
    PAYMENT_AWAITING_APPROVAL = "payment.awaiting_approval"


class Channel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"


PERSONAL_CHANNELS = (Channel.IN_APP, Channel.EMAIL)
TENANT_CHANNELS = (Channel.SLACK, Channel.TEAMS)


@dataclass(frozen=True)
class Preferences:
    """A user's choices. In-app can be muted per event type; e-mail can be switched off entirely."""

    email: bool = True
    muted_events: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TenantChannel:
    """A tenant's incoming webhook (Slack or Teams) and the event types it receives."""

    channel: Channel
    events: frozenset[str]
    enabled: bool = True


@dataclass(frozen=True)
class Recipient:
    user_id: str
    email: str | None
    locale: str | None
    preferences: Preferences = field(default_factory=Preferences)


@dataclass(frozen=True)
class PlannedDelivery:
    channel: Channel
    delivery_key: str
    user_id: str | None = None  # None for tenant (webhook) channels
    address: str | None = None  # e-mail address for the e-mail channel
    locale: str = "en"


def delivery_key(event_id: str, channel: Channel, recipient: str | None) -> str:
    return f"{event_id}:{channel.value}:{recipient or 'tenant'}"


def plan_deliveries(
    *,
    event_id: str,
    event_type: str,
    recipients: list[Recipient],
    tenant_channels: list[TenantChannel],
    actor_user_id: str | None,
    email_available: bool,
    webhooks_available: bool,
    tenant_locale: str = "en",
) -> list[PlannedDelivery]:
    """Fan one event out to deliveries.

    - The person who caused the event is never notified about it.
    - In-app is on unless the user muted this event type; e-mail needs the user's opt-in (default on),
      an address, and e-mail being configured for the deployment.
    - Slack/Teams go to the tenant's channels subscribed to this event, if webhooks are enabled.
    - Duplicate recipients collapse to one delivery per channel.
    """
    plans: dict[str, PlannedDelivery] = {}
    for r in recipients:
        if actor_user_id is not None and r.user_id == actor_user_id:
            continue
        locale = r.locale or tenant_locale
        if event_type not in r.preferences.muted_events:
            key = delivery_key(event_id, Channel.IN_APP, r.user_id)
            plans[key] = PlannedDelivery(Channel.IN_APP, key, user_id=r.user_id, locale=locale)
            if email_available and r.preferences.email and r.email:
                key = delivery_key(event_id, Channel.EMAIL, r.user_id)
                plans[key] = PlannedDelivery(
                    Channel.EMAIL, key, user_id=r.user_id, address=r.email, locale=locale
                )
    if webhooks_available:
        for tc in tenant_channels:
            if tc.enabled and event_type in tc.events:
                key = delivery_key(event_id, tc.channel, None)
                plans[key] = PlannedDelivery(tc.channel, key, locale=tenant_locale)
    return list(plans.values())


def backoff(attempt: int, *, base_seconds: int = 30, cap_seconds: int = 3600, key: str = "") -> timedelta:
    """Delay before retry `attempt` (1-based): exponential, capped, with deterministic jitter (±20%).

    The jitter comes from the delivery key, so retries of different deliveries spread out while the
    schedule stays reproducible in tests.
    """
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    raw = min(cap_seconds, base_seconds * 2 ** (attempt - 1))
    digest = int(hashlib.sha256(f"{key}:{attempt}".encode()).hexdigest()[:8], 16)
    jitter = (digest % 41 - 20) / 100  # -0.20 .. +0.20
    return timedelta(seconds=max(1, round(raw * (1 + jitter))))


def is_dead(attempts: int, max_attempts: int) -> bool:
    """After `max_attempts` failed attempts a delivery is dead-lettered for an operator to review."""
    return attempts >= max_attempts


def webhook_host(url: str, allowed_hosts: list[str]) -> str:
    """Validate a tenant-supplied incoming-webhook URL and return its host, or raise ValueError.

    Only https, no credentials in the URL, the default port, and a host on the allow-list (exact, or a
    subdomain of an entry written as ".example.com"). The worker re-checks before every post, so a URL
    stored before the allow-list changed cannot be used to reach other hosts (SSRF).
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise ValueError("The webhook must be an https:// URL without credentials")
    if parts.port not in (None, 443):
        raise ValueError("The webhook must use the default https port")
    for allowed in (a.lower() for a in allowed_hosts):
        if (allowed.startswith(".") and host.endswith(allowed)) or host == allowed:
            return host
    raise ValueError(f"Webhook host {host!r} is not an allowed Slack or Teams host")


# --- Message text -------------------------------------------------------------------------------

# (subject, body) per language. Placeholders come from the event payload; unknown ones render "—".
_TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        EventType.APPROVAL_ROUTED: (
            "Approval needed: {number}",
            "{number} “{title}” ({amount}) is waiting for your approval as {role}.",
        ),
        EventType.APPROVAL_ESCALATED: (
            "Escalated to you: {number}",
            "{number} “{title}” passed its approval deadline and was escalated to you.",
        ),
        EventType.APPROVAL_DELEGATED: (
            "Delegated to you: {number}",
            "{delegator} delegated the {role} approval of {number} “{title}” to you.",
        ),
        EventType.REQUEST_REJECTED: ("Rejected: {number}", "{number} “{title}” was rejected: {reason}"),
        EventType.REQUEST_APPROVED: (
            "Approved: {number}",
            "{number} “{title}” ({amount}) is fully approved.",
        ),
        EventType.PO_SENT: (
            "Purchase order sent: {po_number}",
            "{po_number} for {number} “{title}” was sent to the vendor.",
        ),
        EventType.INVOICE_MISMATCH: (
            "Invoice needs review: {invoice_number}",
            "Invoice {invoice_number} on {po_number} did not match ({match_status}).",
        ),
        EventType.PAYMENT_AWAITING_APPROVAL: (
            "Payment awaiting approval: {invoice_number}",
            "A payment of {amount} for invoice {invoice_number} is waiting for finance approval.",
        ),
    },
    "ja": {
        EventType.APPROVAL_ROUTED: (
            "承認依頼：{number}",
            "{number}「{title}」（{amount}）が{role}としてのあなたの承認を待っています。",
        ),
        EventType.APPROVAL_ESCALATED: (
            "エスカレーション：{number}",
            "{number}「{title}」が承認期限を過ぎたため、あなたにエスカレーションされました。",
        ),
        EventType.APPROVAL_DELEGATED: (
            "承認の委任：{number}",
            "{delegator} さんが {number}「{title}」の{role}承認をあなたに委任しました。",
        ),
        EventType.REQUEST_REJECTED: (
            "却下：{number}",
            "{number}「{title}」は却下されました。理由：{reason}",
        ),
        EventType.REQUEST_APPROVED: (
            "承認完了：{number}",
            "{number}「{title}」（{amount}）の承認が完了しました。",
        ),
        EventType.PO_SENT: (
            "発注書送付：{po_number}",
            "{number}「{title}」の発注書 {po_number} を取引先に送付しました。",
        ),
        EventType.INVOICE_MISMATCH: (
            "請求書の確認依頼：{invoice_number}",
            "{po_number} の請求書 {invoice_number} が一致しませんでした（{match_status}）。",
        ),
        EventType.PAYMENT_AWAITING_APPROVAL: (
            "支払承認待ち：{invoice_number}",
            "請求書 {invoice_number} の支払 {amount} が経理の承認を待っています。",
        ),
    },
}

_ROLE_NAMES = {
    "en": {
        "manager": "Manager",
        "department_head": "Department Head",
        "procurement_officer": "Procurement Officer",
        "finance_manager": "Finance Manager",
    },
    "ja": {
        "manager": "上長",
        "department_head": "部門長",
        "procurement_officer": "調達担当",
        "finance_manager": "経理責任者",
    },
}


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "—"


def render(event_type: str, payload: dict, locale: str | None) -> tuple[str, str]:
    """Subject and body for an event in the recipient's language (English when unsupported)."""
    lang = locale if locale in _TEMPLATES else "en"
    subject, body = _TEMPLATES[lang].get(event_type, ("ProcuraX: {event}", "{event}"))
    values = _Missing({k: v for k, v in payload.items() if isinstance(v, str | int | float)})
    values["event"] = event_type
    if "role" in payload:
        values["role"] = _ROLE_NAMES[lang].get(str(payload["role"]), str(payload["role"]))
    return subject.format_map(values), body.format_map(values)
