"""End-to-end purchase-request workflow through the public API, including the exception
paths in the spec: rejection, reopen/resubmit, policy block, budget exceeded, split
purchase, emergency purchase, cancellation, and concurrent approvals."""

import asyncio

from httpx import AsyncClient
from sqlalchemy import text

from app.core.db import system_session, utcnow
from app.modules.procurement.escalations import run_sla_escalations
from tests.helpers import API, act, build_org, create_pr, create_vendor, current_round, submit


async def _get(client: AsyncClient, org, pr: dict, actor: str = "employee") -> dict:  # noqa: ANN001
    r = await client.get(f"{API}/purchase-requests/{pr['id']}", headers=org[actor].headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_manager_tier_happy_path_with_audit_trail(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await create_pr(client, org, amount="120000")
    assert pr["status"] == "draft"
    assert pr["allowed_actions"] == ["edit", "submit", "cancel"]

    pr = await submit(client, org, pr)
    assert pr["status"] == "pending_approval"
    [step] = current_round(pr)
    assert step["approver_role"] == "manager"
    assert step["assigned_user_id"] == org["manager"].user_id
    assert step["status"] == "pending" and step["due_at"] is not None
    assert pr["policy_evaluation"]["outcome"] == "requires_approval"
    assert pr["policy_version"] == 1

    # Only the assigned approver can act; the requester never can.
    assert (await act(client, org, pr, "employee")).status_code == 403
    assert (await act(client, org, pr, "head")).status_code == 403
    manager_view = await _get(client, org, pr, "manager")
    assert manager_view["allowed_actions"] == ["approve", "reject"]

    r = await act(client, org, pr, "manager", comment="OK within team budget")
    assert r.status_code == 200, r.text
    pr = r.json()
    assert pr["status"] == "approved"
    assert pr["decided_at"] is not None

    timeline = (
        await client.get(f"{API}/purchase-requests/{pr['id']}/timeline", headers=org["employee"].headers)
    ).json()
    actions = [e["action"] for e in timeline]
    assert actions == [
        "purchase_request.created",
        "purchase_request.submitted",
        "approval.routed",
        "approval.approved",
        "purchase_request.approved",
    ]
    routed = timeline[2]
    assert routed["actor_type"] == "policy_engine" and routed["actor_user_id"] is None
    assert timeline[3]["actor_user_id"] == org["manager"].user_id
    assert all(e["request_id"] for e in timeline)


async def test_low_value_request_is_auto_approved_by_policy(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await submit(client, org, await create_pr(client, org, amount="12000"))
    assert pr["status"] == "approved"
    assert pr["approvals"] == []
    timeline = (
        await client.get(f"{API}/purchase-requests/{pr['id']}/timeline", headers=org["employee"].headers)
    ).json()
    auto = timeline[-1]
    assert auto["action"] == "purchase_request.auto_approved"
    assert auto["actor_type"] == "policy_engine"


async def test_scheduled_sla_escalation_reassigns_once_to_manager(client: AsyncClient, admin_engine) -> None:
    org = await build_org(client)
    request = await submit(client, org, await create_pr(client, org, amount="120000"))
    [step] = current_round(request)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE approvals SET due_at = :past WHERE id = :approval_id"),
            {"past": utcnow(), "approval_id": step["id"]},
        )

    session = system_session()
    try:
        first = await run_sla_escalations(session)
    finally:
        await session.close()
    assert first == {"escalated": 1, "no_target": 0}
    refreshed = await client.get(f"{API}/purchase-requests/{request['id']}", headers=org["head"].headers)
    assert refreshed.status_code == 200, refreshed.text
    escalated_step = next(s for s in refreshed.json()["approvals"] if s["id"] == step["id"])
    assert escalated_step["assigned_user_id"] == org["head"].user_id
    assert escalated_step["escalation_count"] == 1
    assert escalated_step["is_overdue"] is False
    inbox = await client.get(f"{API}/approvals/inbox", headers=org["head"].headers)
    assert any(item["approval_id"] == step["id"] for item in inbox.json())

    session = system_session()
    try:
        second = await run_sla_escalations(session)
    finally:
        await session.close()
    assert second == {"escalated": 0, "no_target": 0}


async def test_multi_step_chain_runs_sequentially(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await submit(client, org, await create_pr(client, org, amount="1500000"))
    steps = current_round(pr)
    assert [s["approver_role"] for s in steps] == ["manager", "department_head", "procurement_officer"]
    assert [s["status"] for s in steps] == ["pending", "waiting", "waiting"]
    # Later approvers cannot jump the queue.
    assert (await act(client, org, pr, "head")).status_code == 403
    assert (await act(client, org, pr, "procurement")).status_code == 403

    pr = (await act(client, org, pr, "manager")).json()
    assert [s["status"] for s in current_round(pr)] == ["approved", "pending", "waiting"]
    pr = (await act(client, org, pr, "head")).json()
    assert pr["status"] == "pending_approval"
    # Procurement step is a pool: any procurement officer except the requester.
    pr = (await act(client, org, pr, "procurement")).json()
    assert pr["status"] == "approved"
    assert [s["status"] for s in current_round(pr)] == ["approved"] * 3


async def test_reject_reopen_resubmit_keeps_history(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await submit(client, org, await create_pr(client, org, amount="700000"))
    # Rejection needs a reason.
    assert (await act(client, org, pr, "manager", "reject")).status_code == 422
    r = await act(client, org, pr, "manager", "reject", comment="Get a second quote first")
    pr = r.json()
    assert pr["status"] == "rejected"
    assert [s["status"] for s in current_round(pr)] == ["rejected", "cancelled"]
    assert pr["allowed_actions"] == []  # manager's view

    pr = await _get(client, org, pr)
    assert set(pr["allowed_actions"]) == {"reopen", "cancel"}
    r = await client.post(f"{API}/purchase-requests/{pr['id']}/reopen", headers=org["employee"].headers)
    assert r.json()["status"] == "draft"

    r = await client.patch(
        f"{API}/purchase-requests/{pr['id']}",
        headers=org["employee"].headers,
        json={"items": [{"description": "Cheaper option", "quantity": "2", "unit_price": "150000"}]},
    )
    assert r.json()["estimated_total"] == "300000.00"
    pr = await submit(client, org, r.json())
    assert pr["submission_count"] == 2
    assert [s["approver_role"] for s in current_round(pr)] == ["manager"]
    rounds = {s["round"] for s in pr["approvals"]}
    assert rounds == {1, 2}  # round 1 history retained


async def test_blocked_vendor_blocks_then_request_can_be_fixed(client: AsyncClient) -> None:
    org = await build_org(client)
    blocked = await create_vendor(client, org, status="blocked")
    good = await create_vendor(client, org)
    pr = await submit(client, org, await create_pr(client, org, amount="50000", vendor_id=blocked["id"]))
    assert pr["status"] == "policy_blocked"
    assert pr["approvals"] == []
    blocking = [h for h in pr["policy_evaluation"]["hits"] if h["severity"] == "block"]
    assert [h["rule_id"] for h in blocking] == ["VEN-001"]

    await client.post(f"{API}/purchase-requests/{pr['id']}/reopen", headers=org["employee"].headers)
    await client.patch(
        f"{API}/purchase-requests/{pr['id']}",
        headers=org["employee"].headers,
        json={"preferred_vendor_id": good["id"]},
    )
    pr = await submit(client, org, pr)
    assert pr["status"] == "pending_approval"


async def test_budget_exceeded_routes_to_finance(client: AsyncClient) -> None:
    org = await build_org(client, budget="200000")
    pr = await submit(client, org, await create_pr(client, org, amount="250000"))
    assert [s["approver_role"] for s in current_round(pr)] == ["manager", "finance_manager"]
    assert pr["policy_evaluation"]["budget"]["status"] == "exceeded"
    pr = (await act(client, org, pr, "manager")).json()
    # Procurement officers are not finance: pool membership is by role.
    assert (await act(client, org, pr, "procurement")).status_code == 403
    pr = (await act(client, org, pr, "finance", comment="Approved from contingency")).json()
    assert pr["status"] == "approved"


async def test_missing_budget_routes_to_finance(client: AsyncClient) -> None:
    org = await build_org(client, budget=None)
    pr = await submit(client, org, await create_pr(client, org, amount="80000"))
    assert pr["policy_evaluation"]["budget"]["status"] == "no_budget"
    assert current_round(pr)[-1]["rule_ids"] == ["BUD-001"]


async def test_concurrent_final_approvals_cannot_overspend_budget(client: AsyncClient) -> None:
    """Two requests each fit the budget alone but not together. Approving both at the same
    moment must not overspend: the budget-row lock routes the second one to finance."""
    org = await build_org(client, budget="1000000")
    first = await submit(client, org, await create_pr(client, org, amount="400000"))
    second = await submit(client, org, await create_pr(client, org, "peer", amount="400000"), "peer")
    # Both in budget at submission (nothing committed yet)…
    assert first["policy_evaluation"]["budget"]["status"] == "within_budget"
    assert second["policy_evaluation"]["budget"]["status"] == "within_budget"
    # …then a third approved request consumes most of the budget.
    third = await submit(client, org, await create_pr(client, org, "manager", amount="450000"), "manager")
    third = (await act(client, org, third, "head")).json()
    assert third["status"] == "approved"

    r1, r2 = await asyncio.gather(act(client, org, first, "manager"), act(client, org, second, "manager"))
    assert r1.status_code == r2.status_code == 200
    statuses = sorted([r1.json()["status"], r2.json()["status"]])
    assert statuses == ["approved", "pending_approval"]
    held = r1.json() if r1.json()["status"] == "pending_approval" else r2.json()
    added = current_round(held)[-1]
    assert added["approver_role"] == "finance_manager"
    assert added["rule_ids"] == ["BUD-002"]

    budgets = (await client.get(f"{API}/budgets", headers=org["finance"].headers)).json()
    assert float(budgets[0]["committed"]) <= 1000000


async def test_split_purchase_is_escalated(client: AsyncClient) -> None:
    org = await build_org(client)
    first = await submit(client, org, await create_pr(client, org, amount="25000"))
    assert first["status"] == "approved"  # under the auto-approval limit
    second = await submit(client, org, await create_pr(client, org, amount="25000"))
    assert second["status"] == "pending_approval"
    [step] = current_round(second)
    assert step["approver_role"] == "manager"
    assert step["rule_ids"] == ["SPL-001"]
    hit = next(h for h in second["policy_evaluation"]["hits"] if h["rule_id"] == "SPL-001")
    assert hit["evidence"]["related_requests"] == [first["number"]]


async def test_emergency_purchase_goes_straight_to_department_head(client: AsyncClient) -> None:
    org = await build_org(client)
    short = await submit(
        client, org, await create_pr(client, org, amount="300000", emergency=True, justification="asap")
    )
    assert short["status"] == "policy_blocked"
    pr = await create_pr(
        client,
        org,
        amount="300000",
        emergency=True,
        justification="Cold-storage compressor failed overnight; stock at risk without same-day replacement.",
    )
    pr = await submit(client, org, pr)
    assert [s["approver_role"] for s in current_round(pr)] == ["department_head"]
    assert current_round(pr)[0]["assigned_user_id"] == org["head"].user_id
    assert "EMG-001" in {h["rule_id"] for h in pr["policy_evaluation"]["hits"]}


async def test_same_person_is_not_asked_twice(client: AsyncClient) -> None:
    """The manager reports to nobody in this chain: when the requester's line manager is
    also the department head, the department-head step is skipped after their approval."""
    org = await build_org(client)
    # The manager requests: their line manager is the head, who also heads the department.
    pr = await submit(client, org, await create_pr(client, org, "manager", amount="700000"), "manager")
    steps = current_round(pr)
    assert [s["assigned_user_id"] for s in steps] == [org["head"].user_id, org["head"].user_id]
    pr = (await act(client, org, pr, "head")).json()
    assert pr["status"] == "approved"
    assert [s["status"] for s in current_round(pr)] == ["approved", "skipped"]


async def test_cancellation_rules(client: AsyncClient) -> None:
    org = await build_org(client)
    pending = await submit(client, org, await create_pr(client, org, amount="100000"))
    assert (await act(client, org, pending, "peer", "cancel", reason="not mine")).status_code == 404
    r = await act(client, org, pending, "employee", "cancel", reason="No longer needed")
    assert r.json()["status"] == "cancelled"
    assert [s["status"] for s in current_round(r.json())] == ["cancelled"]
    assert (await act(client, org, pending, "manager")).status_code == 409

    approved = await submit(client, org, await create_pr(client, org, amount="10000"))
    assert approved["status"] == "approved"
    assert (await act(client, org, approved, "employee", "cancel", reason="changed mind")).status_code == 403
    r = await act(client, org, approved, "procurement", "cancel", reason="Duplicate of existing order")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


async def test_invalid_transitions_are_conflicts(client: AsyncClient) -> None:
    org = await build_org(client)
    draft = await create_pr(client, org)
    assert (await act(client, org, draft, "manager")).status_code == 409
    submitted = await submit(client, org, draft)
    r = await client.patch(f"{API}/purchase-requests/{draft['id']}/submit", headers=org["employee"].headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state_transition"
    r = await client.patch(
        f"{API}/purchase-requests/{submitted['id']}",
        headers=org["employee"].headers,
        json={"title": "Changed"},
    )
    assert r.status_code == 409


async def test_only_requester_can_edit_or_submit(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await create_pr(client, org)
    r = await client.patch(f"{API}/purchase-requests/{pr['id']}/submit", headers=org["manager"].headers)
    assert r.status_code == 403
    r = await client.patch(
        f"{API}/purchase-requests/{pr['id']}", headers=org["manager"].headers, json={"title": "X y"}
    )
    assert r.status_code == 403


async def test_policy_preview_does_not_change_state(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await create_pr(client, org, amount="2000000", category="software")
    r = await client.post(
        f"{API}/purchase-requests/{pr['id']}/policy-preview", headers=org["employee"].headers
    )
    assert r.status_code == 200
    preview = r.json()
    assert preview["outcome"] == "requires_approval"
    assert [s["role"] for s in preview["required_approvals"]] == [
        "manager",
        "department_head",
        "procurement_officer",
    ]
    assert (await _get(client, org, pr))["status"] == "draft"


async def test_visibility_follows_reporting_lines(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await create_pr(client, org)
    get = lambda actor: client.get(f"{API}/purchase-requests/{pr['id']}", headers=org[actor].headers)  # noqa: E731
    assert (await get("peer")).status_code == 404  # colleague
    assert (await get("manager")).status_code == 200  # direct report
    assert (await get("head")).status_code == 200  # heads the charged department
    assert (await get("analyst")).status_code == 200  # read-all
    assert (await get("admin")).status_code == 200
    peer_list = (await client.get(f"{API}/purchase-requests", headers=org["peer"].headers)).json()
    assert pr["id"] not in {p["id"] for p in peer_list["items"]}


async def test_inbox_lists_only_actionable_steps(client: AsyncClient) -> None:
    org = await build_org(client)
    pr = await submit(client, org, await create_pr(client, org, amount="80000", category="software"))
    manager_inbox = (await client.get(f"{API}/approvals/inbox", headers=org["manager"].headers)).json()
    assert [i["purchase_request_id"] for i in manager_inbox] == [pr["id"]]
    assert manager_inbox[0]["assigned_to_me"] is True
    # Procurement's step is still waiting → not in their inbox yet.
    assert (await client.get(f"{API}/approvals/inbox", headers=org["procurement"].headers)).json() == []
    await act(client, org, pr, "manager")
    procurement_inbox = (
        await client.get(f"{API}/approvals/inbox", headers=org["procurement"].headers)
    ).json()
    assert [i["approver_role"] for i in procurement_inbox] == ["procurement_officer"]
    assert procurement_inbox[0]["assigned_to_me"] is False  # pool step
    assert (await client.get(f"{API}/approvals/inbox", headers=org["manager"].headers)).json() == []


async def test_stats_are_computed_from_live_data(client: AsyncClient) -> None:
    org = await build_org(client)
    await submit(client, org, await create_pr(client, org, amount="10000"))  # auto-approved
    await submit(client, org, await create_pr(client, org, amount="90000"))  # pending
    await create_pr(client, org)  # draft
    stats = (await client.get(f"{API}/purchase-requests/stats", headers=org["employee"].headers)).json()
    assert stats["by_status"]["approved"] == 1
    assert stats["by_status"]["pending_approval"] == 1
    assert stats["by_status"]["draft"] == 1
    assert stats["my_open"] == 2
    assert stats["approved_value_current_fy"] == "10000.00"
    manager_stats = (
        await client.get(f"{API}/purchase-requests/stats", headers=org["manager"].headers)
    ).json()
    assert manager_stats["awaiting_my_approval"] == 1


async def test_sla_sweep_skips_while_another_process_holds_the_lock(admin_engine) -> None:
    from app.modules.procurement.escalations import SLA_SWEEP_LOCK_KEY, run_sla_sweep_once

    async with admin_engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": SLA_SWEEP_LOCK_KEY})
        try:
            assert await run_sla_sweep_once() is None
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SLA_SWEEP_LOCK_KEY})
    assert await run_sla_sweep_once() is not None
