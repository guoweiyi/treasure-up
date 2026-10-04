"""Add revocable, hash-only integration tokens with durable quota counters."""
from alembic import op
import sqlalchemy as sa

revision = "c8412d60fa31"
down_revision = "b762ac483f10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("integration_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("source_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("minute_started_at", sa.DateTime(timezone=True)),
        sa.Column("minute_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_started_at", sa.DateTime(timezone=True)),
        sa.Column("day_videos", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_integration_tokens_user_id", "integration_tokens", ["user_id"])
    op.create_index("ix_integration_tokens_account_id", "integration_tokens", ["account_id"])


def downgrade():
    op.drop_table("integration_tokens")
