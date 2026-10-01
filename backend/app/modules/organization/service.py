import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.principal import Principal
from app.domain.workflow.purchase_request import PRStatus
from app.modules.audit import service as audit
from app.modules.identity.models import Organization, User
from app.modules.organization.models import Budget, CostCenter, Department
from app.modules.organization.schemas import (
    BudgetCreate,
    BudgetOut,
    BudgetUpdate,
    CostCenterCreate,
    CostCenterUpdate,
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
)
from app.modules.procurement.models import PurchaseRequest

# Approved requests consume budget ("commitments"). P6+ adds PO / invoice actuals.
COMMITTED_STATUSES = (PRStatus.APPROVED.value,)
PENDING_STATUSES = (PRStatus.PENDING_APPROVAL.value,)


async def _flush_or_conflict(session: AsyncSession, message: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(message) from exc


# --- Departments --------------------------------------------------------------------------


async def _get_department(session: AsyncSession, org_id: uuid.UUID, dept_id: uuid.UUID) -> Department:
    dept = await session.get(Department, dept_id)
    if dept is None or dept.org_id != org_id or dept.deleted_at is not None:
        raise NotFoundError("Department not found")
    return dept


async def _check_user(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    user = await session.get(User, user_id)
    if user is None or user.org_id != org_id or user.deleted_at is not None:
        raise NotFoundError("User not found")


async def list_departments(session: AsyncSession, org_id: uuid.UUID) -> list[DepartmentOut]:
    rows = await session.execute(
        select(Department, User.full_name)
        .outerjoin(User, User.id == Department.head_user_id)
        .where(Department.org_id == org_id, Department.deleted_at.is_(None))
        .order_by(Department.code)
    )
    out = []
    for dept, head_name in rows.all():
        item = DepartmentOut.model_validate(dept)
        item.head_name = head_name
        out.append(item)
    return out


async def create_department(
    session: AsyncSession, principal: Principal, data: DepartmentCreate
) -> Department:
    await _check_user(session, principal.org_id, data.head_user_id)
    dept = Department(org_id=principal.org_id, **data.model_dump())
    session.add(dept)
    await _flush_or_conflict(session, f"Department code '{data.code}' already exists")
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="department.created",
        entity_type="department",
        entity_id=dept.id,
        summary=f"Created department {dept.code} – {dept.name}",
    )
    await session.commit()
    return dept


async def update_department(
    session: AsyncSession, principal: Principal, dept_id: uuid.UUID, data: DepartmentUpdate
) -> Department:
    dept = await _get_department(session, principal.org_id, dept_id)
    fields = data.model_dump(exclude_unset=True)
    if "head_user_id" in fields:
        await _check_user(session, principal.org_id, data.head_user_id)
    before = {k: str(getattr(dept, k)) for k in fields}
    for key, value in fields.items():
        setattr(dept, key, value)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="department.updated",
        entity_type="department",
        entity_id=dept.id,
        summary=f"Updated department {dept.code}",
        changes={"before": before, "after": {k: str(v) for k, v in fields.items()}},
    )
    await session.commit()
    return dept


# --- Cost centres --------------------------------------------------------------------------


async def get_cost_center(session: AsyncSession, org_id: uuid.UUID, cc_id: uuid.UUID) -> CostCenter:
    cc = await session.get(CostCenter, cc_id)
    if cc is None or cc.org_id != org_id:
        raise NotFoundError("Cost centre not found")
    return cc


async def list_cost_centers(
    session: AsyncSession, org_id: uuid.UUID, department_id: uuid.UUID | None = None
) -> list[CostCenter]:
    query = select(CostCenter).where(CostCenter.org_id == org_id)
    if department_id:
        query = query.where(CostCenter.department_id == department_id)
    return list(await session.scalars(query.order_by(CostCenter.code)))


async def create_cost_center(
    session: AsyncSession, principal: Principal, data: CostCenterCreate
) -> CostCenter:
    await _get_department(session, principal.org_id, data.department_id)
    cc = CostCenter(org_id=principal.org_id, **data.model_dump())
    session.add(cc)
    await _flush_or_conflict(session, f"Cost centre code '{data.code}' already exists")
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="cost_center.created",
        entity_type="cost_center",
        entity_id=cc.id,
        summary=f"Created cost centre {cc.code}",
    )
    await session.commit()
    return cc


async def update_cost_center(
    session: AsyncSession, principal: Principal, cc_id: uuid.UUID, data: CostCenterUpdate
) -> CostCenter:
    cc = await get_cost_center(session, principal.org_id, cc_id)
    fields = data.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in fields.items():
        setattr(cc, key, value)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="cost_center.updated",
        entity_type="cost_center",
        entity_id=cc.id,
        summary=f"Updated cost centre {cc.code}",
        changes={"after": fields},
    )
    await session.commit()
    return cc


# --- Budgets ---------------------------------------------------------------------------------


async def committed_amount(
    session: AsyncSession,
    org_id: uuid.UUID,
    cost_center_id: uuid.UUID,
    fiscal_year: int,
    *,
    exclude_request_id: uuid.UUID | None = None,
) -> Decimal:
    query = select(func.coalesce(func.sum(PurchaseRequest.estimated_total), 0)).where(
        PurchaseRequest.org_id == org_id,
        PurchaseRequest.cost_center_id == cost_center_id,
        PurchaseRequest.fiscal_year == fiscal_year,
        PurchaseRequest.status.in_(COMMITTED_STATUSES),
    )
    if exclude_request_id:
        query = query.where(PurchaseRequest.id != exclude_request_id)
    return Decimal(await session.scalar(query) or 0)


async def find_budget(
    session: AsyncSession,
    org_id: uuid.UUID,
    cost_center_id: uuid.UUID,
    fiscal_year: int,
    *,
    for_update: bool = False,
) -> Budget | None:
    query = select(Budget).where(
        Budget.org_id == org_id, Budget.cost_center_id == cost_center_id, Budget.fiscal_year == fiscal_year
    )
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


async def list_budgets(
    session: AsyncSession, org_id: uuid.UUID, fiscal_year: int | None = None
) -> list[BudgetOut]:
    sums = (
        select(
            PurchaseRequest.cost_center_id,
            PurchaseRequest.fiscal_year,
            func.coalesce(
                func.sum(PurchaseRequest.estimated_total).filter(
                    PurchaseRequest.status.in_(COMMITTED_STATUSES)
                ),
                0,
            ).label("committed"),
            func.coalesce(
                func.sum(PurchaseRequest.estimated_total).filter(
                    PurchaseRequest.status.in_(PENDING_STATUSES)
                ),
                0,
            ).label("pending"),
        )
        .where(PurchaseRequest.org_id == org_id)
        .group_by(PurchaseRequest.cost_center_id, PurchaseRequest.fiscal_year)
        .subquery()
    )
    query = (
        select(Budget, CostCenter, sums.c.committed, sums.c.pending)
        .join(CostCenter, CostCenter.id == Budget.cost_center_id)
        .outerjoin(
            sums,
            (sums.c.cost_center_id == Budget.cost_center_id) & (sums.c.fiscal_year == Budget.fiscal_year),
        )
        .where(Budget.org_id == org_id)
    )
    if fiscal_year:
        query = query.where(Budget.fiscal_year == fiscal_year)
    rows = await session.execute(query.order_by(Budget.fiscal_year.desc(), CostCenter.code))
    return [_budget_out(b, cc, committed, pending) for b, cc, committed, pending in rows.all()]


def _budget_out(b: Budget, cc: CostCenter, committed: Decimal | None, pending: Decimal | None) -> BudgetOut:
    committed = Decimal(committed or 0)
    pending = Decimal(pending or 0)
    return BudgetOut(
        id=b.id,
        cost_center_id=b.cost_center_id,
        cost_center_code=cc.code,
        cost_center_name=cc.name,
        department_id=cc.department_id,
        fiscal_year=b.fiscal_year,
        amount=b.amount,
        currency=b.currency,
        notes=b.notes,
        committed=committed,
        pending=pending,
        available=b.amount - committed,
        utilization=(committed / b.amount).quantize(Decimal("0.0001")) if b.amount > 0 else None,
    )


async def create_budget(session: AsyncSession, principal: Principal, data: BudgetCreate) -> BudgetOut:
    cc = await get_cost_center(session, principal.org_id, data.cost_center_id)
    org = await session.get(Organization, principal.org_id)
    assert org is not None
    budget = Budget(
        org_id=principal.org_id,
        cost_center_id=cc.id,
        fiscal_year=data.fiscal_year,
        amount=data.amount,
        currency=org.base_currency,
        notes=data.notes,
    )
    session.add(budget)
    await _flush_or_conflict(session, f"A FY{data.fiscal_year} budget already exists for {cc.code}")
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="budget.created",
        entity_type="budget",
        entity_id=budget.id,
        summary=f"Created FY{data.fiscal_year} budget for {cc.code}: {data.amount}",
        changes={"after": {"amount": str(data.amount)}},
    )
    await session.commit()
    committed = await committed_amount(session, principal.org_id, cc.id, budget.fiscal_year)
    return _budget_out(budget, cc, committed, Decimal(0))


async def update_budget(
    session: AsyncSession, principal: Principal, budget_id: uuid.UUID, data: BudgetUpdate
) -> BudgetOut:
    budget = await session.get(Budget, budget_id, with_for_update=True)
    if budget is None or budget.org_id != principal.org_id:
        raise NotFoundError("Budget not found")
    before = str(budget.amount)
    budget.amount = data.amount
    if data.notes is not None:
        budget.notes = data.notes
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="budget.updated",
        entity_type="budget",
        entity_id=budget.id,
        summary=f"Changed FY{budget.fiscal_year} budget from {before} to {data.amount}",
        changes={"before": {"amount": before}, "after": {"amount": str(data.amount)}},
    )
    await session.commit()
    cc = await get_cost_center(session, principal.org_id, budget.cost_center_id)
    committed = await committed_amount(session, principal.org_id, cc.id, budget.fiscal_year)
    return _budget_out(budget, cc, committed, None)
