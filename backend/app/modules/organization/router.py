import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, CurrentUser, requires
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.modules.organization import service
from app.modules.organization.schemas import (
    BudgetCreate,
    BudgetOut,
    BudgetUpdate,
    CostCenterCreate,
    CostCenterOut,
    CostCenterUpdate,
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
)

router = APIRouter(tags=["organisation structure"])

StructureAdmin = Annotated[Principal, Depends(requires(Permission.ORG_STRUCTURE_MANAGE))]


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(session: DB, principal: CurrentUser) -> list[DepartmentOut]:
    return await service.list_departments(session, principal.org_id)


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(data: DepartmentCreate, session: DB, principal: StructureAdmin) -> DepartmentOut:
    return DepartmentOut.model_validate(await service.create_department(session, principal, data))


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: uuid.UUID, data: DepartmentUpdate, session: DB, principal: StructureAdmin
) -> DepartmentOut:
    return DepartmentOut.model_validate(
        await service.update_department(session, principal, department_id, data)
    )


@router.get("/cost-centers", response_model=list[CostCenterOut])
async def list_cost_centers(
    session: DB, principal: CurrentUser, department_id: uuid.UUID | None = None
) -> list[CostCenterOut]:
    rows = await service.list_cost_centers(session, principal.org_id, department_id)
    return [CostCenterOut.model_validate(r) for r in rows]


@router.post("/cost-centers", response_model=CostCenterOut, status_code=status.HTTP_201_CREATED)
async def create_cost_center(data: CostCenterCreate, session: DB, principal: StructureAdmin) -> CostCenterOut:
    return CostCenterOut.model_validate(await service.create_cost_center(session, principal, data))


@router.patch("/cost-centers/{cost_center_id}", response_model=CostCenterOut)
async def update_cost_center(
    cost_center_id: uuid.UUID, data: CostCenterUpdate, session: DB, principal: StructureAdmin
) -> CostCenterOut:
    return CostCenterOut.model_validate(
        await service.update_cost_center(session, principal, cost_center_id, data)
    )


@router.get("/budgets", response_model=list[BudgetOut])
async def list_budgets(
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.BUDGET_READ))],
    fiscal_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[BudgetOut]:
    return await service.list_budgets(session, principal.org_id, fiscal_year)


@router.post("/budgets", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.BUDGET_MANAGE))],
) -> BudgetOut:
    return await service.create_budget(session, principal, data)


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: uuid.UUID,
    data: BudgetUpdate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.BUDGET_MANAGE))],
) -> BudgetOut:
    return await service.update_budget(session, principal, budget_id, data)
