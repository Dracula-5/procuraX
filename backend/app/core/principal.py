import uuid
from dataclasses import dataclass

from app.core.errors import PermissionDeniedError
from app.domain.rbac import Permission, Role


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Built once per request from the token + database."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    email: str
    full_name: str
    roles: frozenset[Role]
    permissions: frozenset[Permission]
    department_id: uuid.UUID | None = None

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def has_role(self, role: Role | str) -> bool:
        return Role(role) in self.roles

    def require(self, *permissions: Permission) -> None:
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise PermissionDeniedError(
                "You do not have permission to perform this action",
                details={"missing_permissions": [p.value for p in missing]},
            )
