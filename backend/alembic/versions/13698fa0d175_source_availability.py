"""Bounded source-availability check history, independent of archived media."""
from alembic import op
import sqlalchemy as sa

revision = "13698fa0d175"
down_revision = "02587e9fc064"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("source_availability", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("videos", "source_availability")
