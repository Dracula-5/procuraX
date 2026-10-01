from httpx import AsyncClient

from tests.helpers import API, add_user, build_org, create_pr, submit


async def test_named_approver_delegates_and_delegate_can_act(client: AsyncClient) -> None:
    org = await build_org(client)
    delegate = await add_user(client, org, "backup_manager", ["manager", "employee"])
    request = await submit(client, org, await create_pr(client, org, amount="600000"))
    manager_step = next(s for s in request["approvals"] if s["status"] == "pending")
    result = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": manager_step["id"], "delegate_user_id": delegate.user_id},
    )
    assert result.status_code == 201, result.text
    inbox = await client.get(f"{API}/approvals/inbox", headers=delegate.headers)
    assert manager_step["id"] in {row["approval_id"] for row in inbox.json()}

    original = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers
    )
    assert original.status_code == 403
    approved = await client.post(f"{API}/purchase-requests/{request['id']}/approve", headers=delegate.headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending_approval"


async def test_delegator_can_revoke_before_decision(client: AsyncClient) -> None:
    org = await build_org(client)
    delegate = await add_user(client, org, "backup_manager", ["manager", "employee"])
    request = await submit(client, org, await create_pr(client, org, amount="600000"))
    manager_step = next(s for s in request["approvals"] if s["status"] == "pending")
    created = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": manager_step["id"], "delegate_user_id": delegate.user_id},
    )
    assert created.status_code == 201
    revoked = await client.post(
        f"{API}/approvals/delegations/{created.json()['id']}/revoke", headers=org["manager"].headers
    )
    assert revoked.status_code == 200, revoked.text
    original = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers
    )
    assert original.status_code == 200
