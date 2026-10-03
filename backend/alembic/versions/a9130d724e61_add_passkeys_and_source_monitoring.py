"""Add passkey credentials, one-use challenges, and source observations."""
from alembic import op
import sqlalchemy as sa

revision = "a9130d724e61"
down_revision = "8f240c645d02"
branch_labels = None
depends_on = None


def entity():
    return [sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table("passkey_credentials", *entity(),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=False, unique=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("aaguid", sa.String(36)),
        sa.Column("device_type", sa.String(32), nullable=False),
        sa.Column("backed_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_passkey_credentials_user_id", "passkey_credentials", ["user_id"])
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("passkey_credential_id", sa.String(36)))
        batch.create_foreign_key("fk_sessions_passkey_credential", "passkey_credentials", ["passkey_credential_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_sessions_passkey_credential_id", ["passkey_credential_id"])
    op.create_table("passkey_challenges", *entity(),
        sa.Column("challenge", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE")),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE")),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(500), nullable=False),
        sa.Column("rp_id", sa.String(253), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_passkey_challenges_expires_at", "passkey_challenges", ["expires_at"])
    op.create_table("passkey_attempts", *entity(),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False))
    op.create_index("ix_passkey_attempts_client_hash", "passkey_attempts", ["client_hash"])
    op.add_column("collections", sa.Column("monitor_state", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("collection_items", sa.Column("observation", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("collection_items", "observation")
    op.drop_column("collections", "monitor_state")
    op.drop_table("passkey_attempts")
    op.drop_table("passkey_challenges")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_index("ix_sessions_passkey_credential_id")
        batch.drop_constraint("fk_sessions_passkey_credential", type_="foreignkey")
        batch.drop_column("passkey_credential_id")
    op.drop_table("passkey_credentials")
