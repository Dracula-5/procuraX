"""APIs behind the Phase 1 screens: language preference, delegation screen, bank and payer settings."""

from httpx import AsyncClient

from tests.api.test_payment_export import BANK
from tests.helpers import API, add_user, build_org, create_pr, create_vendor, register_org, submit

PAYER = {**BANK, "consignor_code": "1234567890", "consignor_name_kana": "ミナトセイサクシヨ"}


# --- Language preference ----------------------------------------------------------------------


async def test_language_preference_is_saved_per_user(client: AsyncClient) -> None:
    org = await register_org(client)
    me = await client.get(f"{API}/auth/me", headers=org.admin.headers)
    assert me.json()["locale"] is None  # not chosen: the browser language applies

    saved = await client.patch(f"{API}/auth/me/preferences", headers=org.admin.headers, json={"locale": "ja"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["locale"] == "ja"
    assert (await client.get(f"{API}/auth/me", headers=org.admin.headers)).json()["locale"] == "ja"

    other = await add_user(client, org, "colleague", ["employee"])
    assert (await client.get(f"{API}/auth/me", headers=other.headers)).json()["locale"] is None

    cleared = await client.patch(f"{API}/auth/me/preferences", headers=org.admin.headers, json={"locale": None})
    assert cleared.json()["locale"] is None


async def test_language_preference_rejects_unsupported_values(client: AsyncClient) -> None:
    org = await register_org(client)
    for body in ({"locale": "fr"}, {"locale": "ja", "theme": "dark"}, {}):
        r = await client.patch(f"{API}/auth/me/preferences", headers=org.admin.headers, json=body)
        assert r.status_code == 422, body
    assert (await client.patch(f"{API}/auth/me/preferences", json={"locale": "ja"})).status_code == 401


# --- Delegation screen ------------------------------------------------------------------------


async def _pending_manager_step(client: AsyncClient):  # noqa: ANN202
    org = await build_org(client)
    backup = await add_user(client, org, "backup_manager", ["manager", "employee"])
    await add_user(client, org, "plain_employee", ["employee"])
    request = await submit(client, org, await create_pr(client, org, amount="600000"))
    step = next(s for s in request["approvals"] if s["status"] == "pending")
    return org, backup, request, step


async def test_delegable_steps_and_candidates_follow_the_eligibility_rules(client: AsyncClient) -> None:
    org, backup, request, step = await _pending_manager_step(client)

    delegable = await client.get(f"{API}/approvals/delegable", headers=org["manager"].headers)
    assert delegable.status_code == 200, delegable.text
    rows = {row["approval_id"]: row for row in delegable.json()}
    assert step["id"] in rows
    assert rows[step["id"]]["number"] == request["number"]
    assert rows[step["id"]]["approver_role"] == "manager"
    # Another approver has nothing named to them yet.
    assert (await client.get(f"{API}/approvals/delegable", headers=backup.headers)).json() == []

    candidates = await client.get(
        f"{API}/approvals/{step['id']}/delegate-candidates", headers=org["manager"].headers
    )
    assert candidates.status_code == 200, candidates.text
    ids = {c["id"] for c in candidates.json()}
    assert backup.user_id in ids
    # Never the caller, the requester, or someone without the step's approver role.
    assert org["manager"].user_id not in ids
    assert org["employee"].user_id not in ids
    assert org["plain_employee"].user_id not in ids
    assert org["procurement"].user_id not in ids

    # Only the named approver may list candidates for the step.
    refused = await client.get(f"{API}/approvals/{step['id']}/delegate-candidates", headers=backup.headers)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "delegation_not_named_approver"


async def test_refusals_carry_a_reason_code(client: AsyncClient) -> None:
    org, backup, _request, step = await _pending_manager_step(client)
    cases = [
        (org["manager"].user_id, "delegation_self"),
        (org["employee"].user_id, "delegation_requester"),
        (org["plain_employee"].user_id, "delegation_delegate_ineligible"),
    ]
    for delegate_id, reason in cases:
        r = await client.post(
            f"{API}/approvals/delegations",
            headers=org["manager"].headers,
            json={"approval_id": step["id"], "delegate_user_id": delegate_id},
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["details"]["reason"] == reason
    ok = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": step["id"], "delegate_user_id": backup.user_id},
    )
    assert ok.status_code == 201, ok.text
    # The step is now the delegate's; the delegator is no longer its named approver.
    again = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": step["id"], "delegate_user_id": backup.user_id},
    )
    assert again.json()["error"]["details"]["reason"] == "delegation_not_named_approver"


async def test_delegation_list_shows_names_and_revocability(client: AsyncClient) -> None:
    org, backup, request, step = await _pending_manager_step(client)
    created = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": step["id"], "delegate_user_id": backup.user_id},
    )
    assert created.status_code == 201

    given = (await client.get(f"{API}/approvals/delegations", headers=org["manager"].headers)).json()
    assert len(given) == 1
    row = given[0]
    assert row["request_number"] == request["number"]
    assert row["delegator_name"] == "Manager"
    assert row["delegate_name"] == "Backup Manager"
    assert row["step_status"] == "pending"
    assert row["can_revoke"] is True
    # A delegated step is no longer offered for delegation, to anyone.
    for actor in (org["manager"], backup):
        listed = (await client.get(f"{API}/approvals/delegable", headers=actor.headers)).json()
        assert step["id"] not in {r["approval_id"] for r in listed}

    received = (await client.get(f"{API}/approvals/delegations", headers=backup.headers)).json()
    assert [r["id"] for r in received] == [row["id"]]
    assert received[0]["can_revoke"] is False  # only the delegator revokes

    revoked = await client.post(
        f"{API}/approvals/delegations/{row['id']}/revoke", headers=org["manager"].headers
    )
    assert revoked.status_code == 200
    after = (await client.get(f"{API}/approvals/delegations", headers=org["manager"].headers)).json()
    assert after[0]["revoked_at"] is not None
    assert after[0]["can_revoke"] is False
    twice = await client.post(f"{API}/approvals/delegations/{row['id']}/revoke", headers=org["manager"].headers)
    assert twice.status_code == 409
    assert twice.json()["error"]["details"]["reason"] == "delegation_already_revoked"


async def test_delegation_screen_apis_are_tenant_scoped(client: AsyncClient) -> None:
    org, backup, _request, step = await _pending_manager_step(client)
    other = await build_org(client, name="Other Tenant K.K.")
    r = await client.get(f"{API}/approvals/{step['id']}/delegate-candidates", headers=other["manager"].headers)
    assert r.status_code == 404
    created = await client.post(
        f"{API}/approvals/delegations",
        headers=org["manager"].headers,
        json={"approval_id": step["id"], "delegate_user_id": backup.user_id},
    )
    assert created.status_code == 201
    assert (await client.get(f"{API}/approvals/delegations", headers=other["manager"].headers)).json() == []
    foreign = await client.post(
        f"{API}/approvals/delegations/{created.json()['id']}/revoke", headers=other["manager"].headers
    )
    assert foreign.status_code == 404


# --- Bank account and payer settings ----------------------------------------------------------


async def test_bank_account_errors_name_the_field_and_reason(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    kanji = await client.put(
        f"{API}/vendors/{vendor['id']}/bank-account",
        headers=org["procurement"].headers,
        json={**BANK, "holder_name_kana": "株式会社ミナト"},
    )
    assert kanji.status_code == 422
    assert kanji.json()["error"]["details"] == {"reason": "zengin_invalid_characters", "field": "holder_name_kana"}

    long_name = await client.put(
        f"{API}/vendors/{vendor['id']}/bank-account",
        headers=org["procurement"].headers,
        json={**BANK, "bank_name_kana": "アイウエオカキクケコサシスセソタ"},
    )
    assert long_name.json()["error"]["details"] == {"reason": "zengin_too_long", "field": "bank_name_kana"}

    saved = await client.put(
        f"{API}/vendors/{vendor['id']}/bank-account", headers=org["procurement"].headers, json=BANK
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["bank_account"]["holder_name_kana"] == "ｶ)ﾐﾅﾄｼﾞﾑ"  # stored in bank form
    assert saved.json()["bank_account"]["account_number"] == "****567"


async def test_payer_settings_validate_the_consignor_at_save_time(client: AsyncClient) -> None:
    org = await build_org(client)
    too_long = await client.put(
        f"{API}/organizations/current/payment-settings",
        headers=org.admin.headers,
        json={**PAYER, "consignor_name_kana": "ア" * 41},
    )
    # Previously accepted here and only rejected when the transfer file was built.
    assert too_long.status_code == 422
    assert too_long.json()["error"]["details"] == {"reason": "zengin_too_long", "field": "consignor_name_kana"}

    saved = await client.put(
        f"{API}/organizations/current/payment-settings", headers=org.admin.headers, json=PAYER
    )
    assert saved.status_code == 200, saved.text


async def test_payer_settings_are_readable_by_admin_and_finance_only(client: AsyncClient) -> None:
    org = await build_org(client)
    put = await client.put(f"{API}/organizations/current/payment-settings", headers=org.admin.headers, json=PAYER)
    assert put.status_code == 200
    for actor in ("admin", "finance"):
        r = await client.get(f"{API}/organizations/current/payment-settings", headers=org[actor].headers)
        assert r.status_code == 200, (actor, r.text)
        assert r.json()["payment_settings"]["account_number"] == "****567"
    for actor in ("employee", "procurement", "analyst"):
        r = await client.get(f"{API}/organizations/current/payment-settings", headers=org[actor].headers)
        assert r.status_code == 403, actor
    # Only the admin may change them.
    assert (
        await client.put(
            f"{API}/organizations/current/payment-settings", headers=org["finance"].headers, json=PAYER
        )
    ).status_code == 403
