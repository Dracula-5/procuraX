"""Transactional outbox, PostgreSQL worker, channels, preferences and tenant isolation of notifications."""

import asyncio
import socket
import uuid
from datetime import timedelta
from email import message_from_bytes
from email.header import decode_header, make_header

import pytest
from aiosmtpd.controller import Controller
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import system_session, utcnow
from app.domain.notifications import EventType
from app.modules.notifications import outbox
from app.modules.notifications.worker import (
    HttpWebhook,
    Transports,
    default_transports,
    deliver_batch,
    fan_out_batch,
    process_once,
)
from app.modules.procurement.escalations import run_sla_escalations
from tests.api.test_p2p_controls import _approved_request, _invoice, _open_order, _receive
from tests.helpers import (
    API,
    add_user,
    build_org,
    create_pr,
    create_vendor,
    current_round,
    register_org,
    submit,
)

SLACK_URL = "https://hooks.slack.com/services/T000/B000/secret-token"
TEAMS_URL = "https://prod-01.japaneast.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=secret"


class RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs: str) -> None:
        self.sent.append(kwargs)


class FailingEmail:
    async def send(self, **kwargs: str) -> None:
        raise ConnectionRefusedError("SMTP server unavailable")


class RecordingWebhook:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def post(self, **kwargs: object) -> None:
        self.posts.append(kwargs)


def _settings(**overrides: object):  # noqa: ANN202
    return get_settings().model_copy(update={"notification_batch_size": 1000, **overrides})


async def drain(settings=None, transports: Transports | None = None) -> None:  # noqa: ANN001
    """Run the worker until both stages are idle (other tests leave events too)."""
    settings = settings or _settings()
    for _ in range(10):
        counts = await process_once(settings, transports or Transports(email=None, webhook=None))
        if not any(counts.values()):
            return


async def _rows(admin_engine, sql: str, **params: object) -> list:  # noqa: ANN001
    async with admin_engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).mappings())


# --- Outbox written with the change ---------------------------------------------------------------


async def test_submission_queues_the_event_in_the_same_transaction(client: AsyncClient, admin_engine) -> None:
    org = await build_org(client)
    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    [event] = await _rows(
        admin_engine,
        "SELECT * FROM notification_outbox WHERE org_id = :org AND aggregate_type = 'approval'",
        org=org.org_id,
    )
    assert event["event_type"] == EventType.APPROVAL_ROUTED
    assert event["status"] == "pending"
    assert event["actor_user_id"] == uuid.UUID(org["employee"].user_id)
    assert event["payload"]["recipient_user_ids"] == [org["manager"].user_id]
    assert event["payload"]["number"] == request["number"]
    assert event["payload"]["amount"] == "¥120,000"

    # A blocked submission routes to nobody, so it queues nothing.
    blocked = await submit(client, org, await create_pr(client, org, amount="50000", cost_center=False))
    assert blocked["status"] == "policy_blocked"
    rows = await _rows(admin_engine, "SELECT id FROM notification_outbox WHERE org_id = :org", org=org.org_id)
    assert len(rows) == 1


async def test_an_emitted_event_rolls_back_with_its_transaction(client: AsyncClient, admin_engine) -> None:
    org = await register_org(client)
    session = system_session()
    try:
        outbox.emit(
            session,
            org_id=uuid.UUID(org.org_id),
            event_type=EventType.REQUEST_APPROVED,
            aggregate_type="purchase_request",
            aggregate_id=uuid.uuid4(),
            actor_user_id=None,
            recipient_user_ids=[uuid.UUID(org.admin.user_id)],
        )
        await session.flush()
        await session.rollback()  # the business change failed
    finally:
        await session.close()
    assert (
        await _rows(admin_engine, "SELECT id FROM notification_outbox WHERE org_id = :org", org=org.org_id)
        == []
    )


# --- In-app delivery ----------------------------------------------------------------------------


async def test_worker_creates_in_app_notifications_for_the_next_approver(client: AsyncClient) -> None:
    org = await build_org(client)
    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    await drain()

    page = (await client.get(f"{API}/notifications", headers=org["manager"].headers)).json()
    assert page["unread_count"] == 1
    [item] = page["items"]
    assert item["event_type"] == "approval.routed"
    assert item["payload"]["number"] == request["number"]
    assert item["link"] == f"/app/requests/{request['id']}"
    # The requester caused the event and is not notified.
    assert (await client.get(f"{API}/notifications", headers=org["employee"].headers)).json()["items"] == []

    read = await client.post(f"{API}/notifications/{item['id']}/read", headers=org["manager"].headers)
    assert read.status_code == 204
    assert (await client.get(f"{API}/notifications", headers=org["manager"].headers)).json()[
        "unread_count"
    ] == 0
    # Someone else's notification is invisible.
    other = await client.post(f"{API}/notifications/{item['id']}/read", headers=org["head"].headers)
    assert other.status_code == 404


async def test_pool_steps_notify_every_holder_of_the_role(client: AsyncClient) -> None:
    org = await build_org(client, budget=None)  # no budget → BUD-001 routes to the finance pool
    second = await add_user(client, org, "finance_two", ["finance_manager"])
    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    assert [s["approver_role"] for s in current_round(request)] == ["manager", "finance_manager"]
    approved = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers
    )
    assert approved.status_code == 200
    await drain()
    for actor in (org["finance"], second):
        events = [
            n["event_type"]
            for n in (await client.get(f"{API}/notifications", headers=actor.headers)).json()["items"]
        ]
        assert events == ["approval.routed"]


async def test_fan_out_is_idempotent_and_safe_with_concurrent_workers(
    client: AsyncClient, admin_engine
) -> None:
    org = await build_org(client)
    for _ in range(3):
        await submit(client, org, await create_pr(client, org, amount="120000"))
    settings = _settings()

    async def one_worker() -> dict[str, int]:
        session = system_session()
        try:
            return await fan_out_batch(session, settings)
        finally:
            await session.close()

    await asyncio.gather(one_worker(), one_worker())
    await drain()
    count_sql = "SELECT count(*) AS n FROM notifications WHERE org_id = :org AND user_id = :user"
    [row] = await _rows(admin_engine, count_sql, org=org.org_id, user=org["manager"].user_id)
    assert row["n"] == 3

    # Replay every event (as after a crash before commit): the delivery keys make it a no-op.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE notification_outbox SET status = 'pending' WHERE org_id = :org"), {"org": org.org_id}
        )
    await drain()
    [row] = await _rows(admin_engine, count_sql, org=org.org_id, user=org["manager"].user_id)
    assert row["n"] == 3


# --- E-mail ---------------------------------------------------------------------------------------


class _Inbox:
    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope) -> str:  # noqa: ANN001, N802 - aiosmtpd hook
        self.messages.append(message_from_bytes(envelope.content))
        return "250 OK"


@pytest.fixture
def smtp_server():  # noqa: ANN201
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    handler = _Inbox()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    yield port, handler
    controller.stop()


async def test_email_is_sent_over_smtp_in_the_recipient_language(client: AsyncClient, smtp_server) -> None:
    port, inbox = smtp_server
    org = await build_org(client)
    saved = await client.patch(
        f"{API}/auth/me/preferences", headers=org["manager"].headers, json={"locale": "ja"}
    )
    assert saved.status_code == 200
    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    settings = _settings(email_enabled=True, smtp_host="127.0.0.1", smtp_port=port)
    await drain(settings, default_transports(settings))

    mine = [m for m in inbox.messages if m["To"] == org["manager"].email]
    assert len(mine) == 1
    subject = str(make_header(decode_header(mine[0]["Subject"])))
    assert subject == f"承認依頼：{request['number']}"
    body = mine[0].get_payload(decode=True).decode("utf-8")
    assert "上長としてのあなたの承認を待っています" in body
    assert f"/app/requests/{request['id']}" in body
    assert mine[0]["Message-ID"]


async def test_failed_deliveries_back_off_then_dead_letter_and_can_be_retried(
    client: AsyncClient, admin_engine
) -> None:
    org = await build_org(client)
    await submit(client, org, await create_pr(client, org, amount="120000"))
    settings = _settings(email_enabled=True, notification_max_attempts=3)
    failing = Transports(email=FailingEmail(), webhook=None)
    await drain(settings, failing)

    sql = "SELECT * FROM notification_deliveries WHERE org_id = :org AND channel = 'email'"
    [delivery] = await _rows(admin_engine, sql, org=org.org_id)
    assert delivery["status"] == "pending" and delivery["attempts"] == 1
    assert "SMTP server unavailable" in delivery["last_error"]
    assert delivery["next_attempt_at"] > utcnow()  # backed off, not retried immediately

    for day in (1, 2):  # each retry happens once its backoff has elapsed
        session = system_session()
        try:
            await deliver_batch(session, settings, failing, now=utcnow() + timedelta(days=day))
        finally:
            await session.close()
    [delivery] = await _rows(admin_engine, sql, org=org.org_id)
    assert delivery["status"] == "dead" and delivery["attempts"] == 3

    dead = await client.get(f"{API}/notification-deliveries", headers=org.admin.headers)
    assert [d["id"] for d in dead.json()] == [str(delivery["id"])]
    assert (
        await client.get(f"{API}/notification-deliveries", headers=org["manager"].headers)
    ).status_code == 403
    retried = await client.post(
        f"{API}/notification-deliveries/{delivery['id']}/retry", headers=org.admin.headers
    )
    assert retried.status_code == 200 and retried.json()["status"] == "pending"
    working = RecordingEmail()
    await drain(settings, Transports(email=working, webhook=None))
    [delivery] = await _rows(admin_engine, sql, org=org.org_id)
    assert delivery["status"] == "sent"
    assert [m["to"] for m in working.sent if m["to"] == org["manager"].email] == [org["manager"].email]


# --- Preferences ----------------------------------------------------------------------------------


async def test_users_choose_their_channels(client: AsyncClient, admin_engine) -> None:
    org = await build_org(client)
    prefs = await client.get(f"{API}/notifications/preferences", headers=org["manager"].headers)
    assert prefs.json()["email_enabled"] is True and "approval.routed" in prefs.json()["event_types"]
    bad = await client.put(
        f"{API}/notifications/preferences",
        headers=org["manager"].headers,
        json={"email_enabled": True, "muted_events": ["x.y"]},
    )
    assert bad.status_code == 422
    muted = await client.put(
        f"{API}/notifications/preferences",
        headers=org["manager"].headers,
        json={"email_enabled": False, "muted_events": ["approval.routed"]},
    )
    assert muted.status_code == 200
    await submit(client, org, await create_pr(client, org, amount="120000"))
    await drain(_settings(email_enabled=True), Transports(email=RecordingEmail(), webhook=None))
    assert (await client.get(f"{API}/notifications", headers=org["manager"].headers)).json()["items"] == []
    rows = await _rows(
        admin_engine, "SELECT id FROM notification_deliveries WHERE org_id = :org", org=org.org_id
    )
    assert rows == []


# --- Slack / Teams ----------------------------------------------------------------------------------


async def test_tenant_webhooks_are_validated_encrypted_and_posted(client: AsyncClient, admin_engine) -> None:
    org = await build_org(client)
    put = lambda kind, body, actor=org.admin: client.put(  # noqa: E731
        f"{API}/notification-channels/{kind}", headers=actor.headers, json=body
    )
    for url in ("http://hooks.slack.com/services/x", "https://attacker.example/hook"):
        refused = await put("slack", {"webhook_url": url, "events": ["approval.routed"]})
        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["reason"] == "webhook_url_not_allowed"
    assert (await put("slack", {"events": ["approval.routed"]})).json()["error"]["details"][
        "reason"
    ] == "webhook_url_required"
    assert (await put("slack", {"webhook_url": SLACK_URL}, actor=org["manager"])).status_code == 403

    saved = await put("slack", {"webhook_url": SLACK_URL, "events": ["approval.routed"]})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "kind": "slack",
        "configured": True,
        "webhook_host": "hooks.slack.com",
        "events": ["approval.routed"],
        "enabled": True,
    }
    assert "secret-token" not in saved.text
    assert (await put("teams", {"webhook_url": TEAMS_URL, "events": ["approval.routed"]})).status_code == 200
    # Changing the event list keeps the stored URL.
    assert (await put("teams", {"events": ["approval.routed", "purchase_order.sent"]})).status_code == 200
    [stored] = await _rows(
        admin_engine,
        "SELECT webhook_url_encrypted FROM notification_channels WHERE org_id = :org AND kind = 'slack'",
        org=org.org_id,
    )
    assert "secret-token" not in stored["webhook_url_encrypted"]  # encrypted at rest
    listed = (await client.get(f"{API}/notification-channels", headers=org.admin.headers)).json()
    assert "secret-token" not in str(listed) and listed["webhooks_available"] is False

    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    hooks = RecordingWebhook()
    await drain(_settings(webhooks_enabled=True), Transports(email=None, webhook=hooks))
    mine = [p for p in hooks.posts if request["number"] in str(p["payload"])]
    assert {p["url"] for p in mine} == {SLACK_URL, TEAMS_URL}
    slack = next(p for p in mine if p["url"] == SLACK_URL)
    assert slack["payload"]["text"].startswith("*承認依頼")  # tenant in Japan → Japanese
    teams = next(p for p in mine if p["url"] == TEAMS_URL)
    assert teams["payload"]["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert slack["idempotency_key"] != teams["idempotency_key"]

    assert (
        await client.delete(f"{API}/notification-channels/slack", headers=org.admin.headers)
    ).status_code == 204
    audit = await client.get(
        f"{API}/audit-logs", headers=org.admin.headers, params={"action": "notification_channel."}
    )
    assert {e["action"] for e in audit.json()["items"]} == {
        "notification_channel.updated",
        "notification_channel.removed",
    }


async def test_http_webhook_transport_posts_json_with_an_idempotency_key() -> None:
    received: dict = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        length = int(
            next(
                line.split(b":")[1]
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length")
            )
        )
        received["head"] = head.decode()
        received["body"] = (await reader.readexactly(length)).decode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await HttpWebhook(get_settings()).post(
            url=f"http://127.0.0.1:{port}/hook", payload={"text": "hi"}, idempotency_key="e1:slack:tenant"
        )
    assert "idempotency-key: e1:slack:tenant" in received["head"].lower()
    assert received["body"] == '{"text":"hi"}'


# --- Every workflow event -----------------------------------------------------------------------------


async def test_each_workflow_change_queues_its_event(client: AsyncClient, admin_engine) -> None:
    org = await build_org(client)
    backup = await add_user(client, org, "backup_manager", ["manager", "employee"])

    async def events() -> dict[str, list]:
        rows = await _rows(
            admin_engine,
            "SELECT event_type, payload FROM notification_outbox WHERE org_id = :org ORDER BY created_at",
            org=org.org_id,
        )
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(r["event_type"], []).append(r["payload"])
        return out

    delegated = await submit(client, org, await create_pr(client, org, amount="120000"))
    step = current_round(delegated)[0]
    r = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": step["id"], "delegate_user_id": backup.user_id},
    )
    assert r.status_code == 201
    assert (await events())["approval.delegated"][0]["recipient_user_ids"] == [backup.user_id]

    rejected = await submit(client, org, await create_pr(client, org, amount="120000"))
    r = await client.post(
        f"{API}/purchase-requests/{rejected['id']}/reject",
        headers=org["manager"].headers,
        json={"comment": "Not this quarter"},
    )
    assert r.status_code == 200
    [payload] = (await events())["purchase_request.rejected"]
    assert (
        payload["recipient_user_ids"] == [org["employee"].user_id] and payload["reason"] == "Not this quarter"
    )

    escalated = await submit(client, org, await create_pr(client, org, amount="120000"))
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE approvals SET due_at = :past WHERE id = :id"),
            {"past": utcnow(), "id": current_round(escalated)[0]["id"]},
        )
    session = system_session()
    try:
        await run_sla_escalations(session)
    finally:
        await session.close()
    assert (await events())["approval.escalated"][0]["recipient_user_ids"] == [org["head"].user_id]

    vendor = await create_vendor(client, org)
    # Above the ¥30,000 auto-approval limit, so a manager approves it (auto-approval notifies nobody).
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Toner", "quantity": "2", "unit_price": "20000"}]
    )
    assert (await events())["purchase_request.approved"][-1]["recipient_user_ids"] == [
        org["employee"].user_id
    ]
    order = await _open_order(client, org, request["id"])
    sent = (await events())["purchase_order.sent"]
    assert sent[0]["po_number"] == order["number"] and sent[0]["recipient_user_ids"] == [
        org["employee"].user_id
    ]
    await _receive(client, org, order, order["items"][0], "2")
    await _invoice(
        client,
        org,
        order,
        "INV-MISMATCH",
        items=[{"purchase_order_item_id": order["items"][0]["id"], "quantity": "2", "unit_price": "21000"}],
    )
    [mismatch] = (await events())["invoice.mismatch"]
    assert mismatch["recipient_roles"] == ["finance_manager"] and mismatch["invoice_number"] == "INV-MISMATCH"

    order2 = await _open_order(
        client,
        org,
        (
            await _approved_request(
                client, org, vendor["id"], [{"description": "Paper", "quantity": "1", "unit_price": "20000"}]
            )
        )["id"],
    )
    await _receive(client, org, order2, order2["items"][0], "1")
    matched = await _invoice(
        client,
        org,
        order2,
        "INV-OK",
        items=[{"purchase_order_item_id": order2["items"][0]["id"], "quantity": "1", "unit_price": "20000"}],
    )
    r = await client.post(
        f"{API}/invoices/{matched['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert r.status_code == 201, r.text
    [payment] = (await events())["payment.awaiting_approval"]
    assert payment["recipient_roles"] == ["finance_manager"] and payment["invoice_number"] == "INV-OK"

    # The audit trail now links created invoices and orders to their rows (the id used to be NULL).
    rows = await _rows(
        admin_engine,
        "SELECT action, entity_id FROM audit_logs "
        "WHERE org_id = :org AND action IN ('invoice.submitted', 'purchase_order.created')",
        org=org.org_id,
    )
    assert rows and all(r["entity_id"] is not None for r in rows)


# --- Tenant isolation (RLS as the runtime role) ----------------------------------------------------------

NOTIFICATION_TABLES = [
    "notification_outbox",
    "notification_deliveries",
    "notifications",
    "notification_preferences",
    "notification_channels",
]


async def _populate(client: AsyncClient, name: str):  # noqa: ANN202
    org = await build_org(client, name=name)
    await client.put(
        f"{API}/notifications/preferences",
        headers=org["manager"].headers,
        json={"email_enabled": True, "muted_events": []},
    )
    await client.put(
        f"{API}/notification-channels/slack",
        headers=org.admin.headers,
        json={"webhook_url": SLACK_URL, "events": ["approval.routed"]},
    )
    await submit(client, org, await create_pr(client, org, amount="120000"))
    return org


async def test_notification_tables_are_isolated_by_rls(client: AsyncClient, app_engine) -> None:
    a = await _populate(client, "Tenant A K.K.")
    b = await _populate(client, "Tenant B K.K.")
    await drain(
        _settings(email_enabled=True, webhooks_enabled=True),
        Transports(email=RecordingEmail(), webhook=RecordingWebhook()),
    )

    async with app_engine.connect() as conn:
        for table in NOTIFICATION_TABLES:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
                orgs = {str(r[0]) for r in await conn.execute(text(f"SELECT DISTINCT org_id FROM {table}"))}  # noqa: S608
                assert orgs == {a.org_id}, table
            async with conn.begin():
                # No tenant context: the policy matches nothing (fails closed).
                assert (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table  # noqa: S608

    # A row cannot be written into another tenant.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
            await conn.execute(
                text(
                    "INSERT INTO notifications (id, org_id, user_id, event_type, payload, delivery_key, created_at) "
                    "VALUES (gen_random_uuid(), :org, :user, 'x', '{}', :key, now())"
                ),
                {"org": b.org_id, "user": b["manager"].user_id, "key": f"rls-{uuid.uuid4()}"},
            )


async def test_runtime_role_cannot_delete_notification_history(app_engine) -> None:
    for table in (
        "notification_outbox",
        "notification_deliveries",
        "notifications",
        "notification_preferences",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_engine.begin() as conn:
                await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
