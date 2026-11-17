"""Transactional outbox, deliveries, in-app notifications, preferences and tenant webhook channels.

All five tables are tenant tables: org_id, ENABLE + FORCE row-level security and the same
`tenant_isolation` policy as 0001 (including the explicit `app.rls_bypass` used by the worker, which
claims due rows across tenants). The runtime role gets only what the code uses: no DELETE except on
notification_channels (an admin can remove a webhook).
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)
GRANTS = {
    "notification_outbox": "SELECT, INSERT, UPDATE",
    "notification_deliveries": "SELECT, INSERT, UPDATE",
    "notifications": "SELECT, INSERT, UPDATE",
    "notification_preferences": "SELECT, INSERT, UPDATE",
    "notification_channels": "SELECT, INSERT, UPDATE, DELETE",
}


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organizations.id"], name=f"fk_{table}_org_id_organizations", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'dead')", name=op.f("ck_notification_outbox_valid_status")
        ),
        _org_fk("notification_outbox"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_notification_outbox_actor_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_outbox"),
    )
    op.create_index("ix_notification_outbox_org_id", "notification_outbox", ["org_id"])
    op.create_index("ix_notification_outbox_due", "notification_outbox", ["status", "next_attempt_at"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("address", sa.String(length=320), nullable=True),
        sa.Column("locale", sa.String(length=5), nullable=False),
        sa.Column("delivery_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dead')", name=op.f("ck_notification_deliveries_valid_status")
        ),
        sa.CheckConstraint(
            "channel IN ('email', 'slack', 'teams')", name=op.f("ck_notification_deliveries_valid_channel")
        ),
        _org_fk("notification_deliveries"),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["notification_outbox.id"],
            name="fk_notification_deliveries_outbox_id_notification_outbox",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], name="fk_notification_deliveries_recipient_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.UniqueConstraint("delivery_key", name="uq_notification_deliveries_delivery_key"),
    )
    op.create_index("ix_notification_deliveries_org_id", "notification_deliveries", ["org_id"])
    op.create_index("ix_notification_deliveries_outbox_id", "notification_deliveries", ["outbox_id"])
    op.create_index(
        "ix_notification_deliveries_due", "notification_deliveries", ["status", "next_attempt_at"]
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("link", sa.String(length=300), nullable=True),
        sa.Column("delivery_key", sa.String(length=200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _org_fk("notifications"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notifications_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("delivery_key", name="uq_notifications_delivery_key"),
    )
    op.create_index("ix_notifications_org_id", "notifications", ["org_id"])
    op.create_index("ix_notifications_user_created", "notifications", ["org_id", "user_id", "created_at"])

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("muted_events", postgresql.ARRAY(sa.String(length=60)), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _org_fk("notification_preferences"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notification_preferences_user_id_users"),
        sa.PrimaryKeyConstraint("user_id", name="pk_notification_preferences"),
    )
    op.create_index("ix_notification_preferences_org_id", "notification_preferences", ["org_id"])

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("webhook_url_encrypted", sa.Text(), nullable=False),
        sa.Column("webhook_host", sa.String(length=255), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String(length=60)), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('slack', 'teams')", name=op.f("ck_notification_channels_valid_kind")),
        _org_fk("notification_channels"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_channels"),
        sa.UniqueConstraint("org_id", "kind", name="uq_notification_channels_org_id_kind"),
    )
    op.create_index("ix_notification_channels_org_id", "notification_channels", ["org_id"])

    role = _role()
    for table, privileges in GRANTS.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
        # Identifiers validated above; DDL cannot take bind parameters.
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
            f"GRANT {privileges} ON {table} TO {role}; END IF; END $$;"
        )


def downgrade() -> None:
    role = _role()
    for table in reversed(GRANTS):
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
            f"REVOKE ALL ON {table} FROM {role}; END IF; END $$;"
        )
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("notification_channels")
    op.drop_table("notification_preferences")
    op.drop_table("notifications")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_outbox")
