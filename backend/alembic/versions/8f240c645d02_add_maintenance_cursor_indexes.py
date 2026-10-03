"""Add indexes for bounded maintenance keyset pagination.

Revision ID: 8f240c645d02
Revises: 564d5950669a
"""
from alembic import op

revision = "8f240c645d02"
down_revision = "564d5950669a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_videos_created_id", "videos", ["created_at", "id"])
    op.create_index("ix_media_variants_kind_created_id", "media_variants", ["kind", "created_at", "id"])
    op.create_index("ix_jobs_kind_target_status", "jobs", ["kind", "target_id", "status"])


def downgrade():
    op.drop_index("ix_jobs_kind_target_status", table_name="jobs")
    op.drop_index("ix_media_variants_kind_created_id", table_name="media_variants")
    op.drop_index("ix_videos_created_id", table_name="videos")
