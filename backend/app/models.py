"""Imports every ORM model so metadata is complete (Alembic autogenerate, tests)."""

from app.modules.audit.models import AuditLog
from app.modules.identity.models import (
    Invitation,
    Organization,
    OrgSequence,
    PermissionDef,
    RoleDef,
    RolePermission,
    User,
    UserRole,
)
from app.modules.organization.models import Budget, CostCenter, Department
from app.modules.procurement.delegations import ApprovalDelegation
from app.modules.procurement.knowledge_models import ProcurementKnowledgeDocument
from app.modules.procurement.models import Approval, ApprovalPolicy, PurchaseRequest, PurchaseRequestItem
from app.modules.procurement.records import (
    GoodsReceipt,
    Invoice,
    Payment,
    PurchaseOrder,
    PurchaseOrderItem,
    VendorQuote,
)
from app.modules.vendors.models import Vendor

# Tables whose rows belong to exactly one tenant; each gets a Row-Level Security policy.
TENANT_TABLES = [
    "users",
    "user_roles",
    "invitations",
    "org_sequences",
    "departments",
    "cost_centers",
    "budgets",
    "vendors",
    "purchase_requests",
    "purchase_request_items",
    "approvals",
    "approval_policies",
    "purchase_orders",
    "purchase_order_items",
    "goods_receipts",
    "invoices",
    "vendor_quotes",
    "payments",
    "approval_delegations",
    "procurement_knowledge_documents",
    "audit_logs",
]

__all__ = [
    "TENANT_TABLES",
    "Approval",
    "ApprovalDelegation",
    "ApprovalPolicy",
    "ProcurementKnowledgeDocument",
    "AuditLog",
    "Budget",
    "CostCenter",
    "Department",
    "Invitation",
    "GoodsReceipt",
    "Invoice",
    "Payment",
    "OrgSequence",
    "Organization",
    "PermissionDef",
    "PurchaseRequest",
    "PurchaseRequestItem",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "VendorQuote",
    "RoleDef",
    "RolePermission",
    "User",
    "UserRole",
    "Vendor",
]
