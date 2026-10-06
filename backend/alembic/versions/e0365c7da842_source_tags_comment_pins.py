"""Keep upstream tags separate from annotations and persist comment pin state."""
from alembic import op
import sqlalchemy as sa

revision = "e0365c7da842"
down_revision = "d9254b6cf731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("source_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("comments", sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index("ix_comment_pinned_order", "comments", ["video_id", "is_pinned", "like_count"])


def downgrade():
    op.drop_index("ix_comment_pinned_order", table_name="comments")
    op.drop_column("comments", "is_pinned")
    op.drop_column("videos", "source_tags")
