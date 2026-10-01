"""Role-based access control: the single source of truth for roles and permissions.

The `roles`, `permissions` and `role_permissions` tables are seeded from this module by
migrations (so the model is queryable and auditable in SQL), and a test asserts the two
never drift. Custom per-tenant roles are future scope.

Coarse-grained permissions are checked here; row-level visibility (e.g. "a manager sees
their direct reports' requests") is applied in the query layer.
"""

from enum import StrEnum


class Permission(StrEnum):
    PR_CREATE = "purchase_request:create"
    PR_READ_OWN = "purchase_request:read_own"
    PR_READ_TEAM = "purchase_request:read_team"
    PR_READ_DEPARTMENT = "purchase_request:read_department"
    PR_READ_ALL = "purchase_request:read_all"
    PR_CANCEL_ANY = "purchase_request:cancel_any"
    APPROVAL_ACT = "approval:act"
    VENDOR_READ = "vendor:read"
    VENDOR_MANAGE = "vendor:manage"
    VENDOR_APPROVE = "vendor:approve"
    VENDOR_PORTAL = "vendor_portal:access"
    BUDGET_READ = "budget:read"
    BUDGET_MANAGE = "budget:manage"
    ORG_STRUCTURE_MANAGE = "org_structure:manage"
    ORG_MANAGE = "organization:manage"
    USER_READ = "user:read"
    USER_MANAGE = "user:manage"
    POLICY_READ = "policy:read"
    POLICY_MANAGE = "policy:manage"
    AUDIT_READ = "audit:read"
    ANALYTICS_READ = "analytics:read"
    PAYMENT_APPROVE = "payment:approve"
    PLATFORM_ADMIN = "platform:admin"


PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.PR_CREATE: "Create and submit own purchase requests",
    Permission.PR_READ_OWN: "View own purchase requests",
    Permission.PR_READ_TEAM: "View purchase requests of direct reports",
    Permission.PR_READ_DEPARTMENT: "View purchase requests of departments the user heads",
    Permission.PR_READ_ALL: "View all purchase requests in the organisation",
    Permission.PR_CANCEL_ANY: "Cancel any purchase request (e.g. approved, not yet ordered)",
    Permission.APPROVAL_ACT: "Approve or reject approval steps assigned to the user or their role",
    Permission.VENDOR_READ: "View vendor master data",
    Permission.VENDOR_MANAGE: "Create and edit vendors",
    Permission.VENDOR_APPROVE: "Change vendor approval status (approve, suspend, block)",
    Permission.VENDOR_PORTAL: "Access the vendor portal for the linked vendor",
    Permission.BUDGET_READ: "View budgets and utilisation",
    Permission.BUDGET_MANAGE: "Create and change budgets",
    Permission.ORG_STRUCTURE_MANAGE: "Manage departments and cost centres",
    Permission.ORG_MANAGE: "Manage organisation settings",
    Permission.USER_READ: "View users in the organisation",
    Permission.USER_MANAGE: "Invite users, assign roles, deactivate users",
    Permission.POLICY_READ: "View procurement approval policy",
    Permission.POLICY_MANAGE: "Publish new procurement approval policy versions",
    Permission.AUDIT_READ: "View the audit trail",
    Permission.ANALYTICS_READ: "View spend analytics",
    Permission.PAYMENT_APPROVE: "Approve or reject payment requests",
    Permission.PLATFORM_ADMIN: "Platform operations (tenant list, usage); no tenant business data",
}


class Role(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    DEPARTMENT_HEAD = "department_head"
    PROCUREMENT_OFFICER = "procurement_officer"
    FINANCE_ANALYST = "finance_analyst"
    FINANCE_MANAGER = "finance_manager"
    VENDOR = "vendor"
    ORG_ADMIN = "org_admin"
    PLATFORM_ADMIN = "platform_admin"
    READ_ONLY_ANALYST = "read_only_analyst"


ROLE_DESCRIPTIONS: dict[Role, tuple[str, str]] = {
    Role.EMPLOYEE: ("Employee / Requester", "Raises purchase requests and tracks their status"),
    Role.MANAGER: ("Manager / Approver", "Approves requests raised by direct reports"),
    Role.DEPARTMENT_HEAD: (
        "Department Head",
        "Approves high-value and emergency requests for the department",
    ),
    Role.PROCUREMENT_OFFICER: ("Procurement Officer", "Owns vendors, sourcing reviews and purchase orders"),
    Role.FINANCE_ANALYST: ("Finance Analyst", "Monitors budgets, invoices and spend"),
    Role.FINANCE_MANAGER: ("Finance Manager", "Approves budget exceptions and payments; owns budgets"),
    Role.VENDOR: ("Vendor", "External supplier user; limited to its own orders and invoices"),
    Role.ORG_ADMIN: ("Organization Admin", "Configures users, structure and policy; does not approve spend"),
    Role.PLATFORM_ADMIN: ("Platform Admin", "Operates the platform; no access to tenant business data"),
    Role.READ_ONLY_ANALYST: ("Read-only Analyst", "Read-only access to requests, vendors and analytics"),
}

P = Permission
_EMPLOYEE = {P.PR_CREATE, P.PR_READ_OWN, P.VENDOR_READ, P.POLICY_READ}
_MANAGER = _EMPLOYEE | {P.PR_READ_TEAM, P.APPROVAL_ACT}
_DEPARTMENT_HEAD = _MANAGER | {P.PR_READ_DEPARTMENT, P.BUDGET_READ, P.ANALYTICS_READ}
_FINANCE_ANALYST = _EMPLOYEE | {P.PR_READ_ALL, P.BUDGET_READ, P.ANALYTICS_READ, P.USER_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.EMPLOYEE: frozenset(_EMPLOYEE),
    Role.MANAGER: frozenset(_MANAGER),
    Role.DEPARTMENT_HEAD: frozenset(_DEPARTMENT_HEAD),
    Role.PROCUREMENT_OFFICER: frozenset(
        _EMPLOYEE
        | {
            P.PR_READ_ALL,
            P.PR_CANCEL_ANY,
            P.APPROVAL_ACT,
            P.VENDOR_MANAGE,
            P.VENDOR_APPROVE,
            P.BUDGET_READ,
            P.ANALYTICS_READ,
            P.USER_READ,
        }
    ),
    Role.FINANCE_ANALYST: frozenset(_FINANCE_ANALYST),
    Role.FINANCE_MANAGER: frozenset(
        _FINANCE_ANALYST | {P.BUDGET_MANAGE, P.APPROVAL_ACT, P.AUDIT_READ, P.PAYMENT_APPROVE}
    ),
    Role.VENDOR: frozenset({P.VENDOR_PORTAL}),
    # Segregation of duties: the admin configures the system but cannot approve spend.
    Role.ORG_ADMIN: frozenset(
        {
            P.ORG_MANAGE,
            P.ORG_STRUCTURE_MANAGE,
            P.USER_READ,
            P.USER_MANAGE,
            P.POLICY_READ,
            P.POLICY_MANAGE,
            P.AUDIT_READ,
            P.BUDGET_READ,
            P.VENDOR_READ,
            P.PR_READ_ALL,
            P.ANALYTICS_READ,
        }
    ),
    Role.PLATFORM_ADMIN: frozenset({P.PLATFORM_ADMIN}),
    Role.READ_ONLY_ANALYST: frozenset(
        {P.PR_READ_ALL, P.VENDOR_READ, P.BUDGET_READ, P.ANALYTICS_READ, P.POLICY_READ}
    ),
}

# Roles an organisation admin may grant through the API. Platform admin is provisioned
# out-of-band (CLI) only; it can never be granted from inside a tenant.
ASSIGNABLE_ROLES: frozenset[Role] = frozenset(set(Role) - {Role.PLATFORM_ADMIN})

# Roles whose holders may act on an approval step routed to that role.
APPROVER_ROLES: frozenset[Role] = frozenset(
    {Role.MANAGER, Role.DEPARTMENT_HEAD, Role.PROCUREMENT_OFFICER, Role.FINANCE_MANAGER}
)


def permissions_for(roles: set[Role] | frozenset[Role]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS[role]
    return frozenset(granted)
