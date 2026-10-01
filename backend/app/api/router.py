from fastapi import APIRouter

from app.modules.audit.router import router as audit_router
from app.modules.identity.router import auth_router, org_router, users_router
from app.modules.organization.router import router as structure_router
from app.modules.procurement.assistant import router as assistant_router
from app.modules.procurement.decision_support import router as decision_support_router
from app.modules.procurement.payment_export import router as payment_export_router
from app.modules.procurement.records_router import router as records_router
from app.modules.procurement.router import approvals_router, policy_router
from app.modules.procurement.router import router as purchase_requests_router
from app.modules.vendors.router import router as vendors_router

api_router = APIRouter()
for r in (
    auth_router,
    org_router,
    users_router,
    structure_router,
    vendors_router,
    purchase_requests_router,
    records_router,
    decision_support_router,
    payment_export_router,
    assistant_router,
    approvals_router,
    policy_router,
    audit_router,
):
    api_router.include_router(r)
