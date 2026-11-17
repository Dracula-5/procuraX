"""Notification rules: who gets what, retry schedule, message text, webhook URL safety, secret storage."""

from datetime import timedelta

import pytest

from app.core import crypto
from app.core.config import get_settings
from app.domain.notifications import (
    Channel,
    EventType,
    Preferences,
    Recipient,
    TenantChannel,
    backoff,
    delivery_key,
    is_dead,
    plan_deliveries,
    render,
    webhook_host,
)

ALLOWED = ["hooks.slack.com", ".webhook.office.com", ".logic.azure.com"]


def _plan(recipients, *, channels=(), actor=None, email=True, webhooks=True, event=EventType.APPROVAL_ROUTED):  # noqa: ANN001, ANN202
    return plan_deliveries(
        event_id="e1",
        event_type=event,
        recipients=list(recipients),
        tenant_channels=list(channels),
        actor_user_id=actor,
        email_available=email,
        webhooks_available=webhooks,
    )


def _keys(plans) -> set[tuple[str, str | None]]:  # noqa: ANN001
    return {(p.channel.value, p.user_id) for p in plans}


def test_each_recipient_gets_in_app_and_email_by_default() -> None:
    plans = _plan([Recipient("u1", "a@x.jp", "ja"), Recipient("u2", "b@x.jp", None)])
    assert _keys(plans) == {("in_app", "u1"), ("email", "u1"), ("in_app", "u2"), ("email", "u2")}
    assert {p.locale for p in plans if p.user_id == "u1"} == {"ja"}
    assert {p.locale for p in plans if p.user_id == "u2"} == {"en"}  # tenant default


def test_the_actor_is_never_notified_about_their_own_action() -> None:
    assert _keys(_plan([Recipient("u1", "a@x.jp", None)], actor="u1")) == set()


def test_preferences_and_deployment_switches_are_respected() -> None:
    muted = Recipient(
        "u1", "a@x.jp", None, Preferences(muted_events=frozenset({EventType.APPROVAL_ROUTED.value}))
    )
    no_email = Recipient("u2", "b@x.jp", None, Preferences(email=False))
    no_address = Recipient("u3", None, None)
    assert _keys(_plan([muted, no_email, no_address])) == {("in_app", "u2"), ("in_app", "u3")}
    assert _keys(_plan([Recipient("u4", "d@x.jp", None)], email=False)) == {("in_app", "u4")}


def test_tenant_webhooks_receive_only_subscribed_events() -> None:
    slack = TenantChannel(Channel.SLACK, frozenset({EventType.APPROVAL_ROUTED.value}))
    teams = TenantChannel(Channel.TEAMS, frozenset({EventType.PO_SENT.value}))
    off = TenantChannel(Channel.TEAMS, frozenset({EventType.APPROVAL_ROUTED.value}), enabled=False)
    assert _keys(_plan([], channels=[slack, teams, off])) == {("slack", None)}
    assert _keys(_plan([], channels=[slack], webhooks=False)) == set()


def test_duplicate_recipients_collapse_and_keys_are_deterministic() -> None:
    plans = _plan([Recipient("u1", "a@x.jp", None), Recipient("u1", "a@x.jp", None)])
    assert len(plans) == 2
    assert {p.delivery_key for p in plans} == {delivery_key("e1", Channel.IN_APP, "u1"), "e1:email:u1"}


def test_backoff_grows_exponentially_with_bounded_jitter_and_a_cap() -> None:
    delays = [backoff(n, key="k") for n in range(1, 9)]
    for n, delay in enumerate(delays, start=1):
        raw = min(3600, 30 * 2 ** (n - 1))
        assert raw * 0.8 <= delay.total_seconds() <= raw * 1.2
    assert delays == [backoff(n, key="k") for n in range(1, 9)]  # reproducible
    assert backoff(30, key="k") <= timedelta(seconds=3600 * 1.2)
    assert backoff(3, key="a") != backoff(3, key="b") or backoff(4, key="a") != backoff(4, key="b")
    with pytest.raises(ValueError):
        backoff(0)


def test_dead_letter_threshold() -> None:
    assert not is_dead(5, 6)
    assert is_dead(6, 6)


def test_messages_render_in_the_recipient_language() -> None:
    payload = {"number": "PR-2026-000042", "title": "Laptops", "amount": "¥600,000", "role": "manager"}
    assert render(EventType.APPROVAL_ROUTED, payload, "en") == (
        "Approval needed: PR-2026-000042",
        "PR-2026-000042 “Laptops” (¥600,000) is waiting for your approval as Manager.",
    )
    subject, body = render(EventType.APPROVAL_ROUTED, payload, "ja")
    assert subject == "承認依頼：PR-2026-000042"
    assert "上長としてのあなたの承認を待っています" in body
    # Unsupported language → English; missing facts render as a dash instead of failing.
    assert render(EventType.REQUEST_REJECTED, {"number": "PR-1"}, "fr")[1] == "PR-1 “—” was rejected: —"


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com/services/T000/B000/XXXX",
        "https://contoso.webhook.office.com/webhookb2/abc",
        "https://prod-12.japaneast.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke",
    ],
)
def test_allowed_webhook_urls(url: str) -> None:
    assert webhook_host(url, ALLOWED)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/services/x",  # not https
        "https://user:pass@hooks.slack.com/services/x",  # credentials
        "https://hooks.slack.com:8443/services/x",  # non-default port
        "https://evil.example.com/hook",  # not allowed
        "https://hooks.slack.com.evil.example/hook",  # suffix trick
        "https://169.254.169.254/latest/meta-data",  # metadata endpoint
        "https://webhook.office.com.evil.example/x",
        "not a url",
    ],
)
def test_webhook_urls_that_could_reach_other_hosts_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        webhook_host(url, ALLOWED)


def test_secrets_round_trip_and_rotate(monkeypatch: pytest.MonkeyPatch) -> None:
    token = crypto.encrypt("https://hooks.slack.com/services/secret")
    assert "secret" not in token
    assert crypto.decrypt(token) == "https://hooks.slack.com/services/secret"
    old = get_settings().data_encryption_key
    # Rotation: a new first key encrypts, the old one still decrypts existing values.
    monkeypatch.setattr(get_settings(), "data_encryption_key", "n" * 40 + "," + old)
    assert crypto.decrypt(token) == "https://hooks.slack.com/services/secret"
    monkeypatch.setattr(get_settings(), "data_encryption_key", "z" * 40)
    with pytest.raises(ValueError):
        crypto.decrypt(token)


def test_production_needs_a_real_data_key_only_when_webhooks_are_on() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    prod = {
        "env": "prod",
        "jwt_secret": "p" * 48,
        "database_url": "postgresql://u:p@db.example/x",
        "public_app_url": "https://procurax.example",
        "demo_mode": False,
    }
    Settings(**prod)  # webhooks off: the live demo keeps starting without the new variable
    with pytest.raises(ValidationError, match="PROCURAX_DATA_ENCRYPTION_KEY must be set"):
        Settings(**prod, webhooks_enabled=True)
    Settings(**prod, webhooks_enabled=True, data_encryption_key="k" * 40)
    with pytest.raises(ValidationError, match="32\+ characters"):
        Settings(**prod, data_encryption_key="short")
