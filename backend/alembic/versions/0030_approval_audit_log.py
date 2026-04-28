"""HITL-01.a — persisted approval audit log.

Revision ID: 0030
Revises: 0029
Create Date: 2026-04-23

The v0 HITL flow kept the approver's identity only inside
``context["approval"]`` — non-queryable, non-enforceable, and
dropped the moment the context snapshot rotated. This migration
lands an ``approval_audit_log`` table tenants can point at for
compliance storytelling: **who** approved / rejected **what**
instance at **which node**, with snapshots of the context before
and after the operator's patch merge.

Fields
------

* ``approver`` is the **claimed** identity the operator sent with
  the resume. We don't have OIDC yet — this is a string attested
  by the caller, protected only by tenant-scoped bearer auth.
  A future ``verified_by`` column can track cryptographic binding
  when IAM lands. Documented honestly in ``codewiki/hitl.md``.
* ``decision`` covers four cases:
    - ``approved`` (happy path)
    - ``rejected`` (operator explicitly rejected)
    - ``timeout_rejected`` (HITL-01.c — scheduler sweep fired)
    - ``timeout_escalated`` (HITL-01.c — sweep sent a notification
      but left the instance suspended)
* ``context_before_json`` / ``context_after_json`` — snapshotted
  synchronously before the Celery resume fires. Lets a reviewer
  answer "what did the approver see when they said yes?" without
  depending on instance_checkpoint rotation.
* ``approvers_allowlist_matched`` — reserved for HITL-01.d. ``None``
  today on every row; set to ``True`` / ``False`` once the
  allowlist enforcement lands.
* ``parent_instance_id`` — reserved for HITL-01.f (bubble-up).
  ``None`` today; lets sub-workflow HITL land without a schema
  change later.

Tenant-scoped RLS mirrors every other tenant-scoped table in the
repo. Index on ``(tenant_id, created_at desc)`` so the list
endpoint and the future dashboard scale past the first few rows
without a re-walk.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(128), nullable=False),
        # HITL-01.f reserved — NULL on all v0 rows.
        sa.Column(
            "parent_instance_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("approver", sa.String(256), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "context_before_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "context_after_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # HITL-01.d reserved — NULL until allowlist enforcement ships.
        sa.Column(
            "approvers_allowlist_matched",
            sa.Boolean(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_approval_audit_tenant_created",
        "approval_audit_log",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_approval_audit_instance",
        "approval_audit_log",
        ["instance_id", "created_at"],
    )

    op.execute("ALTER TABLE approval_audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_approval_audit_log "
        "ON approval_audit_log "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_approval_audit_log "
        "ON approval_audit_log"
    )
    op.drop_index("ix_approval_audit_instance", table_name="approval_audit_log")
    op.drop_index("ix_approval_audit_tenant_created", table_name="approval_audit_log")
    op.drop_table("approval_audit_log")
