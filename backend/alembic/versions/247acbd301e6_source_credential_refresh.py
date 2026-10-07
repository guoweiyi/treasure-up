"""Encrypted refresh credentials and short-lived, session-bound QR authorization."""
from alembic import op
import sqlalchemy as sa

revision = "247acbd301e6"
down_revision = "13698fa0d175"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_pending_encrypted", sa.Text(), nullable=True),
        sa.Column("auto_refresh_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("refresh_phase", sa.String(32), nullable=False, server_default="unsupported"),
        sa.Column("refresh_error_code", sa.String(64), nullable=True),
        sa.Column("refresh_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_refresh_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("source_accounts", column)
    op.create_index("ix_source_accounts_next_refresh_at", "source_accounts", ["next_refresh_at"])
    op.create_table("source_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("source_accounts.id", ondelete="CASCADE")),
        sa.Column("account_generation", sa.String(64)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key_encrypted", sa.Text()),
        sa.Column("credential_encrypted", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
    )
    for name in ("user_id", "session_id", "expires_at"):
        op.create_index("ix_source_authorizations_" + name, "source_authorizations", [name])


def downgrade():
    op.drop_table("source_authorizations")
    op.drop_index("ix_source_accounts_next_refresh_at", table_name="source_accounts")
    for name in ("next_refresh_at", "last_refreshed_at", "last_refresh_check_at", "refresh_failures",
                 "refresh_error_code", "refresh_phase", "auto_refresh_enabled", "refresh_pending_encrypted",
                 "refresh_token_encrypted"):
        op.drop_column("source_accounts", name)
