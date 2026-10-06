"""Personal login tokens remain separate from scoped ingestion credentials.

Revision ID: f1476d8eb953
Revises: e0365c7da842
"""
from alembic import op
import sqlalchemy as sa

revision = "f1476d8eb953"
down_revision = "e0365c7da842"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("identity_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_identity_tokens_user_id", "identity_tokens", ["user_id"])
    op.create_index("ix_identity_tokens_expires_at", "identity_tokens", ["expires_at"])
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("identity_token_id", sa.String(36)))
        batch.create_foreign_key("fk_sessions_identity_token", "identity_tokens", ["identity_token_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_sessions_identity_token_id", ["identity_token_id"])


def downgrade():
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_identity_token_id")
        batch.drop_constraint("fk_sessions_identity_token", type_="foreignkey")
        batch.drop_column("identity_token_id")
    op.drop_table("identity_tokens")
