"""Every AI / ML output, with the model, its version and a reference to the input (insert-only).

Tenant table: org_id, ENABLE + FORCE RLS with 0001's tenant_isolation predicate. Runtime role: SELECT and
INSERT only, so a prediction cannot be edited or removed after the fact.
"""

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TENANT_PREDICATE = (
    "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid "
    "OR current_setting('app.rls_bypass', true) = 'on'"
)


def _role() -> str:
    role = get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):  # interpolated into DDL below
        raise ValueError(f"Invalid database role name: {role!r}")
    return role


def upgrade() -> None:
    op.create_table(
        "ai_predictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("input_ref", sa.String(length=120), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_ai_predictions_org_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_ai_predictions_actor_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_predictions"),
    )
    op.create_index("ix_ai_predictions_org_id", "ai_predictions", ["org_id"])
    op.create_index("ix_ai_predictions_input", "ai_predictions", ["org_id", "input_ref", "kind"])
    op.execute("ALTER TABLE ai_predictions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_predictions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON ai_predictions USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"GRANT SELECT, INSERT ON ai_predictions TO {role}; END IF; END $$;"
    )


def downgrade() -> None:
    role = _role()
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "  # noqa: S608
        f"REVOKE ALL ON ai_predictions FROM {role}; END IF; END $$;"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON ai_predictions")
    op.drop_table("ai_predictions")
