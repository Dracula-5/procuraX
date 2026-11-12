from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import bind_tenant, get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.principal import Principal
from app.core.security import decode_access_token
from app.domain.rbac import Permission, Role, permissions_for
from app.modules.identity.models import User, UserRole

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")

DB = Annotated[AsyncSession, Depends(get_db)]


async def get_principal(
    session: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Authentication required")
    user_id, org_id = decode_access_token(credentials.credentials)

    # From here on every statement in this request runs under the caller's tenant (RLS).
    await bind_tenant(session, org_id)

    user = await session.get(User, user_id)
    if user is None or user.org_id != org_id or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("Account is not active")

    role_keys = await session.scalars(select(UserRole.role_key).where(UserRole.user_id == user_id))
    roles = frozenset(Role(k) for k in role_keys)
    return Principal(
        user_id=user.id,
        org_id=user.org_id,
        email=user.email,
        full_name=user.full_name,
        roles=roles,
        permissions=permissions_for(roles),
        department_id=user.department_id,
    )


CurrentUser = Annotated[Principal, Depends(get_principal)]


def requires(*permissions: Permission):  # noqa: ANN201
    """Endpoint guard: `principal: Principal = Depends(requires(Permission.X))`."""

    async def _guard(principal: CurrentUser) -> Principal:
        principal.require(*permissions)
        return principal

    return _guard


def requires_any(*permissions: Permission):  # noqa: ANN201
    """Endpoint guard satisfied by any one of the permissions (e.g. a screen shared by two roles)."""

    async def _guard(principal: CurrentUser) -> Principal:
        if not any(principal.has(p) for p in permissions):
            raise PermissionDeniedError(
                "You do not have permission to perform this action",
                details={"any_of_permissions": [p.value for p in permissions]},
            )
        return principal

    return _guard
