import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import DB, CurrentUser, requires
from app.core.config import get_settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.principal import Principal
from app.core.rate_limit import FailureThrottle
from app.domain.rbac import Permission
from app.modules.identity import service
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    DemoLoginRequest,
    DemoPersonaOut,
    InvitationCreate,
    InvitationOut,
    LoginRequest,
    MeOut,
    OrgOut,
    OrgUpdate,
    RegisterRequest,
    RoleOut,
    TokenResponse,
    UserOut,
    UserUpdate,
)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
org_router = APIRouter(prefix="/organizations", tags=["organizations"])
users_router = APIRouter(tags=["users & roles"])


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, session: DB) -> TokenResponse:
    """Create a new organisation (tenant) and its first administrator."""
    if not get_settings().allows_registration:
        raise PermissionDeniedError("Self-service registration is disabled on this public demo")
    return await service.register(session, data)


_settings = get_settings()
login_throttle = FailureThrottle(_settings.login_max_failures, _settings.login_lockout_seconds)


@auth_router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request) -> TokenResponse:
    """Password sign-in. Repeated failures from one address are throttled per e-mail and overall."""
    client = request.client.host if request.client else "unknown"
    account_key = f"login:{client}:{data.email.lower()}"
    address_key = f"login:{client}"
    login_throttle.check(
        (account_key, login_throttle.max_failures), (address_key, login_throttle.max_failures * 4)
    )
    try:
        token = await service.login(data.email, data.password)
    except AuthenticationError:
        login_throttle.record_failure(account_key, address_key)
        raise
    login_throttle.reset(account_key)
    return token


@auth_router.post("/accept-invitation", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def accept_invitation(data: AcceptInvitationRequest) -> TokenResponse:
    return await service.accept_invitation(data)


@auth_router.get("/me", response_model=MeOut)
async def me(session: DB, principal: CurrentUser) -> MeOut:
    return await service.me(session, principal)


@auth_router.get("/demo-personas", response_model=list[DemoPersonaOut])
async def demo_personas() -> list[DemoPersonaOut]:
    """Personas of the fictional demo organisation (empty when demo mode is off)."""
    return await service.demo_personas()


@auth_router.post("/demo-login", response_model=TokenResponse)
async def demo_login(data: DemoLoginRequest) -> TokenResponse:
    return await service.demo_login(data.email)


@org_router.get("", response_model=list[dict])
async def list_organizations(session: DB, principal: CurrentUser) -> list[dict]:
    """Platform admins see tenant metadata for all organisations; everyone else sees their own."""
    if principal.has(Permission.PLATFORM_ADMIN):
        return await service.list_all_orgs_for_platform()
    org = await service.current_org(session, principal)
    return [OrgOut.model_validate(org).model_dump()]


@org_router.get("/current", response_model=OrgOut)
async def get_current_org(session: DB, principal: CurrentUser) -> OrgOut:
    return OrgOut.model_validate(await service.current_org(session, principal))


@org_router.patch("/current", response_model=OrgOut)
async def update_current_org(
    data: OrgUpdate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.ORG_MANAGE))],
) -> OrgOut:
    return OrgOut.model_validate(await service.update_org(session, principal, data))


@users_router.get("/roles", response_model=list[RoleOut])
async def list_roles(_: CurrentUser) -> list[RoleOut]:
    return service.list_roles()


@users_router.get("/permissions", response_model=dict[str, str])
async def list_permissions(_: CurrentUser) -> dict[str, str]:
    return service.permission_catalogue()


@users_router.get("/users", response_model=list[UserOut])
async def list_users(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.USER_READ))]
) -> list[UserOut]:
    return await service.list_users(session, principal)


@users_router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> UserOut:
    return await service.update_user(session, principal, user_id, data)


@users_router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))]
) -> list[InvitationOut]:
    return await service.list_invitations(session, principal)


@users_router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    data: InvitationCreate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> InvitationOut:
    return await service.invite(session, principal, data)


@users_router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> None:
    await service.revoke_invitation(session, principal, invitation_id)
