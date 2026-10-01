import re
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import bind_tenant, system_session, utcnow
from app.core.errors import (
    AuthenticationError,
    BusinessRuleViolation,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.principal import Principal
from app.core.security import (
    create_access_token,
    hash_opaque_token,
    hash_password,
    new_opaque_token,
    verify_password,
)
from app.domain.rbac import (
    ASSIGNABLE_ROLES,
    PERMISSION_DESCRIPTIONS,
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    Role,
    permissions_for,
)
from app.modules.audit import service as audit
from app.modules.identity.models import Invitation, Organization, User, UserRole
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    DemoPersonaOut,
    InvitationCreate,
    InvitationOut,
    MeOut,
    OrgOut,
    OrgUpdate,
    RegisterRequest,
    RoleOut,
    TokenResponse,
    UserOut,
    UserUpdate,
)
from app.modules.organization.models import CostCenter, Department
from app.modules.procurement import policy_service

# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{secrets.token_hex(3)}"


async def _roles_of(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(UserRole.role_key).where(UserRole.user_id == user_id).order_by(UserRole.role_key)
    )
    return list(rows)


async def _me(session: AsyncSession, user: User) -> MeOut:
    org = await session.get(Organization, user.org_id)
    assert org is not None
    roles = await _roles_of(session, user.id)
    perms = permissions_for(frozenset(Role(r) for r in roles))
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        job_title=user.job_title,
        department_id=user.department_id,
        manager_id=user.manager_id,
        roles=roles,
        permissions=sorted(p.value for p in perms),
        organization=OrgOut.model_validate(org),
    )


async def _issue_token(session: AsyncSession, user: User) -> TokenResponse:
    token, ttl = create_access_token(user_id=user.id, org_id=user.org_id)
    return TokenResponse(access_token=token, expires_in=ttl, user=await _me(session, user))


async def _email_exists(email: str) -> bool:
    # Global uniqueness check → needs to see all tenants (read-only, single column).
    async with system_session() as s:
        return (await s.scalar(select(User.id).where(User.email == email))) is not None


def _check_assignable(roles: list[Role]) -> None:
    forbidden = [r.value for r in roles if r not in ASSIGNABLE_ROLES]
    if forbidden:
        raise PermissionDeniedError("These roles cannot be granted from an organisation", details=forbidden)


async def _set_roles(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[Role],
    granted_by: uuid.UUID | None,
) -> None:
    await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for role in sorted(set(roles)):
        session.add(UserRole(org_id=org_id, user_id=user_id, role_key=role.value, granted_by_id=granted_by))


# ---------------------------------------------------------------------------------------
# Registration / authentication
# ---------------------------------------------------------------------------------------


async def register(session: AsyncSession, data: RegisterRequest) -> TokenResponse:
    email = data.email.lower()
    if await _email_exists(email):
        raise ConflictError("An account with this e-mail already exists", code="email_taken")

    org_id = uuid.uuid4()
    await bind_tenant(session, org_id)
    org = Organization(
        id=org_id,
        name=data.organization_name.strip(),
        slug=_slugify(data.organization_name),
        base_currency=data.base_currency,
        country=data.country,
        fiscal_year_start_month=data.fiscal_year_start_month,
    )
    session.add(org)
    await session.flush()

    # A default department + cost centre so the first request can be raised immediately.
    dept = Department(org_id=org_id, name="Administration", code="ADM")
    session.add(dept)
    await session.flush()
    session.add(CostCenter(org_id=org_id, department_id=dept.id, code="ADM-001", name="Administration"))

    user = User(
        org_id=org_id,
        email=email,
        full_name=data.full_name.strip(),
        password_hash=hash_password(data.password),
        department_id=dept.id,
        job_title="Organization administrator",
    )
    session.add(user)
    await session.flush()
    await _set_roles(session, org_id, user.id, [Role.ORG_ADMIN, Role.EMPLOYEE], granted_by=None)
    await policy_service.create_initial_policy(session, org_id, data.base_currency, created_by=user.id)

    principal = Principal(
        user_id=user.id,
        org_id=org_id,
        email=email,
        full_name=user.full_name,
        roles=frozenset(),
        permissions=frozenset(),
    )
    await audit.record(
        session,
        org_id=org_id,
        actor=principal,
        action="organization.registered",
        entity_type="organization",
        entity_id=org_id,
        summary=f"Organisation '{org.name}' registered by {email}",
    )
    try:
        await session.commit()
    except IntegrityError as exc:  # concurrent registration with the same e-mail
        await session.rollback()
        raise ConflictError("An account with this e-mail already exists", code="email_taken") from exc
    return await _issue_token(session, user)


async def login(email: str, password: str) -> TokenResponse:
    email = email.lower()
    async with system_session() as s:
        user = await s.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
        valid = verify_password(password, user.password_hash if user else None)
        if user is None:
            raise AuthenticationError("Invalid e-mail or password")
        actor = Principal(
            user_id=user.id,
            org_id=user.org_id,
            email=user.email,
            full_name=user.full_name,
            roles=frozenset(),
            permissions=frozenset(),
        )
        if not valid or not user.is_active:
            await audit.record(
                s,
                org_id=user.org_id,
                actor=actor,
                action="user.login_failed",
                entity_type="user",
                entity_id=user.id,
                summary="Failed login" if valid is False else "Login attempt on inactive account",
            )
            await s.commit()
            raise AuthenticationError("Invalid e-mail or password")
        user.last_login_at = utcnow()
        await audit.record(
            s,
            org_id=user.org_id,
            actor=actor,
            action="user.login",
            entity_type="user",
            entity_id=user.id,
            summary="Signed in",
        )
        await s.commit()
        return await _issue_token(s, user)


async def me(session: AsyncSession, principal: Principal) -> MeOut:
    user = await session.get(User, principal.user_id)
    assert user is not None
    return await _me(session, user)


async def accept_invitation(data: AcceptInvitationRequest) -> TokenResponse:
    token_hash = hash_opaque_token(data.token)
    async with system_session() as s:
        inv = await s.scalar(select(Invitation).where(Invitation.token_hash == token_hash).with_for_update())
        now = utcnow()
        if inv is None or inv.revoked_at is not None or inv.accepted_at is not None or inv.expires_at < now:
            raise NotFoundError("This invitation is invalid or has expired", code="invitation_invalid")
        existing = await s.scalar(select(User.id).where(User.email == inv.email))
        if existing is not None:
            raise ConflictError("An account with this e-mail already exists", code="email_taken")
        user = User(
            org_id=inv.org_id,
            email=inv.email,
            full_name=data.full_name.strip(),
            password_hash=hash_password(data.password),
            department_id=inv.department_id,
            manager_id=inv.manager_id,
            job_title=inv.job_title,
        )
        s.add(user)
        await s.flush()
        await _set_roles(
            s, inv.org_id, user.id, [Role(r) for r in inv.role_keys], granted_by=inv.invited_by_id
        )
        inv.accepted_at = now
        await audit.record(
            s,
            org_id=inv.org_id,
            actor=Principal(
                user_id=user.id,
                org_id=inv.org_id,
                email=user.email,
                full_name=user.full_name,
                roles=frozenset(),
                permissions=frozenset(),
            ),
            action="invitation.accepted",
            entity_type="user",
            entity_id=user.id,
            summary=f"{user.email} accepted invitation with roles {', '.join(inv.role_keys)}",
            meta={"invitation_id": str(inv.id)},
        )
        await s.commit()
        return await _issue_token(s, user)


# ---------------------------------------------------------------------------------------
# Demo tenant (fictional data, clearly labelled; excluded from genuine-usage metrics)
# ---------------------------------------------------------------------------------------


async def demo_personas() -> list[DemoPersonaOut]:
    if not get_settings().demo_mode:
        return []
    async with system_session() as s:
        rows = await s.execute(
            select(User)
            .join(Organization, Organization.id == User.org_id)
            .where(Organization.is_demo.is_(True), User.is_active.is_(True), User.deleted_at.is_(None))
            .order_by(User.email)
        )
        personas = []
        for user in rows.scalars():
            personas.append(
                DemoPersonaOut(
                    email=user.email,
                    full_name=user.full_name,
                    job_title=user.job_title,
                    roles=await _roles_of(s, user.id),
                )
            )
        return personas


async def demo_login(email: str) -> TokenResponse:
    if not get_settings().demo_mode:
        raise NotFoundError("Demo mode is disabled")
    async with system_session() as s:
        user = await s.scalar(
            select(User)
            .join(Organization, Organization.id == User.org_id)
            .where(User.email == email.lower(), Organization.is_demo.is_(True), User.is_active.is_(True))
        )
        if user is None:
            raise NotFoundError("Unknown demo persona")
        user.last_login_at = utcnow()
        await audit.record(
            s,
            org_id=user.org_id,
            actor=None,
            actor_type="demo",
            action="user.demo_login",
            entity_type="user",
            entity_id=user.id,
            summary=f"Demo persona login as {user.email}",
        )
        await s.commit()
        return await _issue_token(s, user)


# ---------------------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------------------


async def current_org(session: AsyncSession, principal: Principal) -> Organization:
    org = await session.get(Organization, principal.org_id)
    if org is None:
        raise NotFoundError("Organisation not found")
    return org


async def update_org(session: AsyncSession, principal: Principal, data: OrgUpdate) -> Organization:
    org = await current_org(session, principal)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    before = {k: getattr(org, k) for k in changes}
    for key, value in changes.items():
        setattr(org, key, value)
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="organization.updated",
        entity_type="organization",
        entity_id=org.id,
        summary="Organisation settings updated",
        changes={"before": before, "after": changes},
    )
    await session.commit()
    return org


async def list_all_orgs_for_platform() -> list[dict]:
    """Platform-admin view: tenant metadata and user counts only, never business data."""
    async with system_session() as s:
        user_counts = select(User.org_id, func.count(User.id).label("users")).group_by(User.org_id).subquery()
        rows = await s.execute(
            select(Organization, func.coalesce(user_counts.c.users, 0))
            .outerjoin(user_counts, user_counts.c.org_id == Organization.id)
            .order_by(Organization.created_at)
        )
        return [
            {**OrgOut.model_validate(org).model_dump(), "user_count": int(count)} for org, count in rows.all()
        ]


# ---------------------------------------------------------------------------------------
# Users, roles, invitations
# ---------------------------------------------------------------------------------------


def list_roles() -> list[RoleOut]:
    return [
        RoleOut(
            key=role.value,
            name=ROLE_DESCRIPTIONS[role][0],
            description=ROLE_DESCRIPTIONS[role][1],
            permissions=sorted(p.value for p in ROLE_PERMISSIONS[role]),
            assignable=role in ASSIGNABLE_ROLES,
        )
        for role in Role
    ]


def permission_catalogue() -> dict[str, str]:
    return {p.value: d for p, d in PERMISSION_DESCRIPTIONS.items()}


async def _user_out(session: AsyncSession, user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        job_title=user.job_title,
        department_id=user.department_id,
        manager_id=user.manager_id,
        roles=await _roles_of(session, user.id),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def list_users(session: AsyncSession, principal: Principal) -> list[UserOut]:
    users = await session.scalars(
        select(User)
        .where(User.org_id == principal.org_id, User.deleted_at.is_(None))
        .order_by(User.full_name)
    )
    all_roles = await session.execute(
        select(UserRole.user_id, UserRole.role_key).where(UserRole.org_id == principal.org_id)
    )
    by_user: dict[uuid.UUID, list[str]] = {}
    for user_id, role_key in all_roles:
        by_user.setdefault(user_id, []).append(role_key)
    return [
        UserOut(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            job_title=u.job_title,
            department_id=u.department_id,
            manager_id=u.manager_id,
            roles=sorted(by_user.get(u.id, [])),
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in users
    ]


async def _get_user(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None or user.org_id != org_id or user.deleted_at is not None:
        raise NotFoundError("User not found")
    return user


async def _check_department(session: AsyncSession, org_id: uuid.UUID, dept_id: uuid.UUID | None) -> None:
    if dept_id is None:
        return
    dept = await session.get(Department, dept_id)
    if dept is None or dept.org_id != org_id or dept.deleted_at is not None:
        raise NotFoundError("Department not found")


async def _check_manager_chain(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None, manager_id: uuid.UUID | None
) -> None:
    if manager_id is None:
        return
    if manager_id == user_id:
        raise BusinessRuleViolation("A user cannot be their own manager")
    await _get_user(session, org_id, manager_id)
    # Walk up the chain to prevent reporting cycles (A → B → A).
    seen: set[uuid.UUID] = set()
    cursor: uuid.UUID | None = manager_id
    while cursor is not None and len(seen) < 100:
        if cursor == user_id:
            raise BusinessRuleViolation("This manager assignment would create a reporting cycle")
        seen.add(cursor)
        cursor = await session.scalar(select(User.manager_id).where(User.id == cursor))


async def update_user(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, data: UserUpdate
) -> UserOut:
    user = await _get_user(session, principal.org_id, user_id)
    fields = data.model_dump(exclude_unset=True)
    before: dict = {}
    after: dict = {}

    if "department_id" in fields:
        await _check_department(session, principal.org_id, data.department_id)
    if "manager_id" in fields:
        await _check_manager_chain(session, principal.org_id, user.id, data.manager_id)
    if data.is_active is False and user.id == principal.user_id:
        raise BusinessRuleViolation("You cannot deactivate your own account")

    for key in ("full_name", "job_title", "department_id", "manager_id", "is_active"):
        if key in fields and getattr(user, key) != fields[key]:
            before[key], after[key] = getattr(user, key), fields[key]
            setattr(user, key, fields[key])

    if data.roles is not None:
        _check_assignable(data.roles)
        current = set(await _roles_of(session, user.id))
        new = {r.value for r in data.roles}
        if Role.ORG_ADMIN.value in current and Role.ORG_ADMIN.value not in new:
            admins = await session.scalar(
                select(func.count())
                .select_from(UserRole)
                .join(User, User.id == UserRole.user_id)
                .where(
                    UserRole.org_id == principal.org_id,
                    UserRole.role_key == Role.ORG_ADMIN.value,
                    User.is_active.is_(True),
                )
            )
            if (admins or 0) <= 1:
                raise BusinessRuleViolation("The organisation must keep at least one active administrator")
        if new != current:
            await _set_roles(session, principal.org_id, user.id, data.roles, granted_by=principal.user_id)
            before["roles"], after["roles"] = sorted(current), sorted(new)

    if after:
        await audit.record(
            session,
            org_id=principal.org_id,
            actor=principal,
            action="user.updated",
            entity_type="user",
            entity_id=user.id,
            summary=f"Updated {user.email}: {', '.join(after)}",
            changes={"before": before, "after": after},
        )
    await session.commit()
    return await _user_out(session, user)


async def invite(session: AsyncSession, principal: Principal, data: InvitationCreate) -> InvitationOut:
    _check_assignable(data.roles)
    email = data.email.lower()
    if await _email_exists(email):
        raise ConflictError("A user with this e-mail already exists", code="email_taken")
    await _check_department(session, principal.org_id, data.department_id)
    if data.manager_id:
        await _get_user(session, principal.org_id, data.manager_id)

    settings = get_settings()
    token, token_hash = new_opaque_token()
    inv = Invitation(
        org_id=principal.org_id,
        email=email,
        full_name=data.full_name,
        role_keys=sorted({r.value for r in data.roles}),
        department_id=data.department_id,
        manager_id=data.manager_id,
        job_title=data.job_title,
        token_hash=token_hash,
        invited_by_id=principal.user_id,
        expires_at=utcnow() + timedelta(hours=settings.invitation_ttl_hours),
    )
    session.add(inv)
    await session.flush()
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="invitation.created",
        entity_type="invitation",
        entity_id=inv.id,
        summary=f"Invited {email} as {', '.join(inv.role_keys)}",
    )
    await session.commit()
    out = _invitation_out(inv)
    out.invite_url = f"{settings.public_app_url}/accept-invite?token={token}"
    return out


def _invitation_out(inv: Invitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        full_name=inv.full_name,
        roles=inv.role_keys,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked_at=inv.revoked_at,
        created_at=inv.created_at,
    )


async def list_invitations(session: AsyncSession, principal: Principal) -> list[InvitationOut]:
    rows = await session.scalars(
        select(Invitation).where(Invitation.org_id == principal.org_id).order_by(Invitation.created_at.desc())
    )
    return [_invitation_out(i) for i in rows]


async def revoke_invitation(session: AsyncSession, principal: Principal, invitation_id: uuid.UUID) -> None:
    inv = await session.get(Invitation, invitation_id)
    if inv is None or inv.org_id != principal.org_id:
        raise NotFoundError("Invitation not found")
    if inv.accepted_at is not None:
        raise ConflictError("Invitation was already accepted")
    inv.revoked_at = utcnow()
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="invitation.revoked",
        entity_type="invitation",
        entity_id=inv.id,
        summary=f"Revoked invitation for {inv.email}",
    )
    await session.commit()
