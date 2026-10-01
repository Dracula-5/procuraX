"""Seed the fictional demo organisation.

    uv run python -m app.scripts.seed_demo            # create if missing
    uv run python -m app.scripts.seed_demo --reset    # delete and recreate

Everything here is FICTIONAL demo data: the organisation, people, vendors and requests
are invented. The tenant is flagged `is_demo`, which (a) enables one-click persona login
and (b) excludes it from genuine-usage metrics. Requests are created through the real
service layer, so they go through the actual policy engine, approval routing and audit.
"""

import argparse
import asyncio
import secrets
import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.db import bind_tenant, get_sessionmaker
from app.core.principal import Principal
from app.core.security import hash_password
from app.domain.fiscal import fiscal_year_of
from app.domain.rbac import Role, permissions_for
from app.models import TENANT_TABLES
from app.modules.identity.models import Organization, User, UserRole
from app.modules.organization.models import Budget, CostCenter, Department
from app.modules.procurement import policy_service
from app.modules.procurement import service as pr_service
from app.modules.procurement.knowledge_models import ProcurementKnowledgeDocument
from app.modules.procurement.schemas import PRCreate, PRItemIn
from app.modules.procurement.service import _local_today
from app.modules.vendors.models import Vendor

DEMO_SLUG = "procurax-demo"
DEMO_NAME = "ProcuraX Demo Manufacturing K.K. (fictional)"
EMAIL_DOMAIN = "procurax-demo.example.com"

DEPARTMENTS = [
    ("ENG", "Engineering"),
    ("IT", "IT & Digital"),
    ("OPS", "Operations"),
    ("MKT", "Marketing"),
    ("FIN", "Finance"),
    ("ADM", "Administration"),
]
BUDGETS = {
    "ENG": "18000000",
    "IT": "25000000",
    "OPS": "30000000",
    "MKT": "12000000",
    "FIN": "4000000",
    "ADM": "6000000",
}

# key, name, title, department, roles, manager key
PEOPLE = [
    ("admin", "Aoi Tanaka", "Organization Administrator", "ADM", [Role.ORG_ADMIN, Role.EMPLOYEE], None),
    ("it_head", "Kenji Watanabe", "Head of IT & Digital", "IT", [Role.DEPARTMENT_HEAD, Role.EMPLOYEE], None),
    (
        "it_manager",
        "Yui Nakamura",
        "IT Infrastructure Manager",
        "IT",
        [Role.MANAGER, Role.EMPLOYEE],
        "it_head",
    ),
    ("it_engineer", "Haruto Sato", "Network Engineer", "IT", [Role.EMPLOYEE], "it_manager"),
    ("ops_head", "Emi Kobayashi", "Head of Operations", "OPS", [Role.DEPARTMENT_HEAD, Role.EMPLOYEE], None),
    ("ops_planner", "Ren Yamamoto", "Production Planner", "OPS", [Role.EMPLOYEE], "ops_head"),
    ("mkt_manager", "Sakura Ito", "Marketing Manager", "MKT", [Role.MANAGER, Role.EMPLOYEE], None),
    ("mkt_specialist", "Daiki Suzuki", "Digital Marketing Specialist", "MKT", [Role.EMPLOYEE], "mkt_manager"),
    ("buyer", "Mei Takahashi", "Procurement Officer", "FIN", [Role.PROCUREMENT_OFFICER, Role.EMPLOYEE], None),
    ("fin_analyst", "Sota Kato", "Finance Analyst", "FIN", [Role.FINANCE_ANALYST], None),
    ("fin_manager", "Hina Yoshida", "Finance Manager", "FIN", [Role.FINANCE_MANAGER, Role.EMPLOYEE], None),
    ("analyst", "Takumi Matsumoto", "Business Analyst (read-only)", "ADM", [Role.READ_ONLY_ANALYST], None),
]
DEMO_GUIDELINE = """\
第1条（見積の取得）100万円以上の購入は、原則として2社以上から見積を取得し、比較結果を購買依頼に添付する。
第2条（適格請求書）請求書には適格請求書発行事業者の登録番号（T＋13桁）、税率ごとの対価の額と消費税額の記載が必要である。
登録番号のない請求書は経理部が例外として審査する。
第3条（検収）物品は受領後5営業日以内に検収を記録する。破損品は検収数量に含めない。
第4条（支払）支払は月末締め翌月末払いとし、全銀フォーマットの振込データで行う。
Article 1 (Quotes): purchases of JPY 1,000,000 or more need quotes from at least two suppliers,
with the comparison attached to the purchase request.
Article 2 (Qualified invoices): invoices must show the supplier's registration number (T + 13 digits)
and the amount and consumption tax per tax rate; invoices without it are reviewed by finance as exceptions.
Article 3 (Receiving): record receipt within five business days; damaged items are not accepted quantity.
Article 4 (Payment): month-end close, paid at the end of the following month by Zengin transfer file.
"""

DEPARTMENT_HEADS = {"IT": "it_head", "OPS": "ops_head", "MKT": "mkt_manager", "FIN": "fin_manager"}

# name, categories, status, risk, contract
VENDORS = [
    ("Kanto Network Solutions (fictional)", ["hardware", "it_services"], "approved", "low", "active"),
    ("Shinsei Cloud Software (fictional)", ["software"], "approved", "low", "active"),
    ("Minato Office Supply (fictional)", ["office_supplies"], "approved", "low", "none"),
    ("Hokuriku Precision Parts (fictional)", ["manufacturing"], "approved", "medium", "active"),
    ("Sakai Logistics (fictional)", ["logistics"], "approved", "low", "expired"),
    ("Nishi Consulting Partners (fictional)", ["professional_services"], "approved", "medium", "active"),
    ("Aozora Travel Services (fictional)", ["travel"], "approved", "low", "none"),
    ("Harbor Facilities Care (fictional)", ["facilities"], "approved", "low", "active"),
    ("Blue Wave Creative (fictional)", ["marketing"], "pending_review", "unknown", "none"),
    ("Tsubame Electronics Trading (fictional)", ["hardware"], "approved", "high", "none"),
    ("Kumo Data Brokers (fictional)", ["it_services"], "blocked", "high", "none"),
]


_TEARDOWN_ORDER = [
    "audit_logs",
    "payments",
    "invoices",
    "goods_receipts",
    "purchase_order_items",
    "purchase_orders",
    "vendor_quotes",
    "approval_delegations",
    "approvals",
    "purchase_request_items",
    "purchase_requests",
    "approval_policies",
    "budgets",
    "invitations",
    "procurement_knowledge_documents",
    "user_roles",
    "org_sequences",
    "users",
    "cost_centers",
    "departments",
    "vendors",
]
assert set(_TEARDOWN_ORDER) == set(TENANT_TABLES), "update the teardown order when adding tenant tables"
_BYPASS_RLS = text("SELECT set_config('app.rls_bypass', 'on', true)")


async def _delete_demo(admin_url: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.begin() as conn:
        # Managed PostgreSQL (e.g. Neon) owners are not superusers, so FORCE RLS applies to them too.
        await conn.execute(_BYPASS_RLS)
        org_id = await conn.scalar(text("SELECT id FROM organizations WHERE slug = :s"), {"s": DEMO_SLUG})
        if org_id:
            # Break the circular references first, then delete children before parents.
            for stmt in (
                "UPDATE departments SET head_user_id = NULL WHERE org_id = :o",
                "UPDATE vendors SET status_changed_by_id = NULL WHERE org_id = :o",
                "UPDATE users SET manager_id = NULL, department_id = NULL, vendor_id = NULL WHERE org_id = :o",
            ):
                await conn.execute(text(stmt), {"o": org_id})
            for table in _TEARDOWN_ORDER:
                await conn.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": org_id})  # noqa: S608
            await conn.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
            print(f"Deleted demo organisation {org_id}")
    await engine.dispose()


def _principal(user: User, roles: list[Role]) -> Principal:
    return Principal(
        user_id=user.id,
        org_id=user.org_id,
        email=user.email,
        full_name=user.full_name,
        roles=frozenset(roles),
        permissions=permissions_for(frozenset(roles)),
        department_id=user.department_id,
    )


async def _seed(session: AsyncSession) -> uuid.UUID:
    org_id = uuid.uuid4()
    await bind_tenant(session, org_id)
    org = Organization(
        id=org_id,
        name=DEMO_NAME,
        slug=DEMO_SLUG,
        base_currency="JPY",
        country="JP",
        fiscal_year_start_month=4,
        timezone="Asia/Tokyo",
        is_demo=True,
        # Fictional bank details (bank code 0999 is a placeholder) so the Zengin export can be demonstrated.
        payment_settings={
            "consignor_code": "0000000001",
            "consignor_name_kana": "ﾌﾟﾛｷﾕﾗｸｽﾃﾞﾓｾｲｿﾞｳ(ﾌｲｸｼﾖﾝ)",
            "bank_code": "0999",
            "bank_name_kana": "ﾃﾞﾓｷﾞﾝｺｳ",
            "branch_code": "001",
            "branch_name_kana": "ﾎﾝﾃﾝ",
            "account_type": "1",
            "account_number": "0000001",
            "holder_name_kana": "ﾌﾟﾛｷﾕﾗｸｽﾃﾞﾓｾｲｿﾞｳ",
        },
    )
    session.add(org)
    await session.flush()

    depts: dict[str, Department] = {}
    ccs: dict[str, CostCenter] = {}
    for code, name in DEPARTMENTS:
        depts[code] = Department(org_id=org_id, code=code, name=name)
        session.add(depts[code])
    await session.flush()
    for code, name in DEPARTMENTS:
        ccs[code] = CostCenter(
            org_id=org_id, department_id=depts[code].id, code=f"{code}-100", name=f"{name} – core"
        )
        session.add(ccs[code])
    await session.flush()

    fiscal_year = fiscal_year_of(_local_today(org), org.fiscal_year_start_month)
    for code, amount in BUDGETS.items():
        session.add(
            Budget(
                org_id=org_id,
                cost_center_id=ccs[code].id,
                fiscal_year=fiscal_year,
                amount=Decimal(amount),
                currency="JPY",
                notes="Fictional demo budget",
            )
        )

    users: dict[str, User] = {}
    roles_by_key: dict[str, list[Role]] = {}
    for key, name, title, dept, roles, _ in PEOPLE:
        users[key] = User(
            org_id=org_id,
            email=f"{key.replace('_', '.')}@{EMAIL_DOMAIN}",
            full_name=name,
            job_title=title,
            department_id=depts[dept].id,
            # Unguessable: demo personas are reached through demo-login only.
            password_hash=hash_password(secrets.token_urlsafe(24)),
        )
        roles_by_key[key] = roles
        session.add(users[key])
    await session.flush()
    for key, *_, manager_key in PEOPLE:
        if manager_key:
            users[key].manager_id = users[manager_key].id
        for role in roles_by_key[key]:
            session.add(UserRole(org_id=org_id, user_id=users[key].id, role_key=role.value))
    for dept, head_key in DEPARTMENT_HEADS.items():
        depts[dept].head_user_id = users[head_key].id
    await policy_service.create_initial_policy(session, org_id, "JPY", created_by=users["admin"].id)

    vendors: dict[str, Vendor] = {}
    for index, (name, categories, status, risk, contract) in enumerate(VENDORS, start=1):
        vendors[name.split(" ")[0]] = Vendor(
            org_id=org_id,
            name=name,
            categories=categories,
            status=status,
            risk_level=risk,
            contract_status=contract,
            country="JP",
            currency="JPY",
            notes="Fictional demo vendor",
            bank_account={
                "bank_code": "0999",
                "bank_name_kana": "ﾃﾞﾓｷﾞﾝｺｳ",
                "branch_code": f"{100 + index:03d}",
                "branch_name_kana": "ﾃﾞﾓｼﾃﾝ",
                "account_type": "1",
                "account_number": f"{index:07d}",
                "holder_name_kana": f"ﾃﾞﾓｼｲﾚｻｷ{index}",
            },
        )
        session.add(vendors[name.split(" ")[0]])
    # A short internal guideline in Japanese and English, so the assistant's cited search can be
    # tried in either language. Written for this fictional company; not an official document.
    session.add(
        ProcurementKnowledgeDocument(
            org_id=org_id,
            title="購買管理規程（抜粋） / Purchasing guideline (excerpt)",
            source_reference="Fictional internal guideline of the demo company",
            content=DEMO_GUIDELINE,
            version=1,
            is_active=True,
            created_by_id=users["admin"].id,
        )
    )
    await session.commit()

    p = {key: _principal(users[key], roles_by_key[key]) for key in users}

    async def request(
        key: str, title: str, category: str, lines: list[tuple[str, str, str]], **kw
    ) -> uuid.UUID:  # noqa: ANN003
        user = users[key]
        dept_code = next(code for code, d in depts.items() if d.id == user.department_id)
        pr = await pr_service.create_request(
            session,
            p[key],
            PRCreate(
                title=title,
                category=category,
                cost_center_id=ccs[dept_code].id,
                justification=kw.pop("justification", f"{title} for {depts[dept_code].name}."),
                items=[
                    PRItemIn(description=d, quantity=Decimal(q), unit_price=Decimal(u)) for d, q, u in lines
                ],
                **kw,
            ),
        )
        return pr.id

    # A realistic spread of states, each produced by the real workflow.
    switches = await request(
        "it_engineer",
        "Core switches for Osaka office refresh",
        "hardware",
        [("48-port L3 switch", "4", "285000"), ("SFP+ transceivers", "16", "9800")],
        preferred_vendor_id=vendors["Kanto"].id,
    )
    await pr_service.submit(session, p["it_engineer"], switches)  # → manager → department head

    toner = await request(
        "mkt_specialist",
        "Printer toner",
        "office_supplies",
        [("Toner cartridge", "2", "8900")],
        preferred_vendor_id=vendors["Minato"].id,
    )
    await pr_service.submit(session, p["mkt_specialist"], toner)  # auto-approved

    saas = await request(
        "it_manager",
        "Endpoint security licences (FY renewal)",
        "software",
        [("EDR licence, annual", "250", "7200")],
        preferred_vendor_id=vendors["Shinsei"].id,
    )
    await pr_service.submit(session, p["it_manager"], saas)  # → department head → procurement
    await pr_service.approve(session, p["it_head"], saas, "Renewal is in the FY plan")

    blocked = await request(
        "it_engineer",
        "Third-party contact list",
        "it_services",
        [("Data extract", "1", "150000")],
        preferred_vendor_id=vendors["Kumo"].id,
    )
    await pr_service.submit(session, p["it_engineer"], blocked)  # → blocked vendor (VEN-001)

    campaign = await request(
        "mkt_specialist",
        "Spring campaign creative production",
        "marketing",
        [("Video production", "1", "680000"), ("Banner set", "1", "120000")],
        preferred_vendor_id=vendors["Blue"].id,
    )
    await pr_service.submit(session, p["mkt_specialist"], campaign)
    await pr_service.reject(
        session, p["mkt_manager"], campaign, "Please get a quote from an approved agency before resubmitting."
    )

    await request(
        "ops_planner",
        "Spare bearings for line 3",
        "manufacturing",
        [("Bearing 6205", "40", "1450")],
        preferred_vendor_id=vendors["Hokuriku"].id,
    )  # left as draft

    pump = await request(
        "ops_planner",
        "Emergency replacement of coolant pump",
        "manufacturing",
        [("Coolant pump assembly", "1", "420000")],
        is_emergency=True,
        justification=(
            "Line 2 coolant pump failed during the night shift; "
            "production stops without a same-day replacement."
        ),
    )
    await pr_service.submit(session, p["ops_planner"], pump)  # → department head only (APR-005)
    return org_id


async def main(reset: bool) -> None:
    settings = get_settings()
    if reset:
        await _delete_demo(settings.database_admin_url)
    engine = create_async_engine(settings.database_admin_url)
    async with engine.begin() as conn:
        await conn.execute(_BYPASS_RLS)
        exists = await conn.scalar(text("SELECT 1 FROM organizations WHERE slug = :s"), {"s": DEMO_SLUG})
    await engine.dispose()
    if exists:
        print("Demo organisation already exists (use --reset to recreate).")
        return
    async with get_sessionmaker()() as session:
        org_id = await _seed(session)
        await bind_tenant(session, org_id)
        count = len((await session.scalars(select(User.id).where(User.org_id == org_id))).all())
    print(f"Seeded fictional demo organisation {org_id} with {count} personas.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset", action="store_true", help="delete and recreate the demo organisation")
    asyncio.run(main(parser.parse_args().reset))
