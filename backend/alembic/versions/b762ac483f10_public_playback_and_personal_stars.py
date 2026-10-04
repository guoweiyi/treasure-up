"""Allow browser-bound guest playback and independent user stars."""
from alembic import op
import sqlalchemy as sa

revision = "b762ac483f10"
down_revision = "a9130d724e61"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("playback_sessions") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=True)
    op.create_table("video_stars",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "video_id"))
    op.create_index("ix_video_stars_user_id", "video_stars", ["user_id"])
    op.create_index("ix_video_stars_video_id", "video_stars", ["video_id"])
    # Keep previous shared stars for existing users when introducing personal stars.
    connection = op.get_bind()
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    targets = connection.execute(sa.text("SELECT u.id, a.video_id FROM app_users u CROSS JOIN video_annotations a WHERE a.starred = true"))
    table = sa.table("video_stars", *[sa.column(name) for name in ("id", "created_at", "updated_at", "user_id", "video_id")])
    for user_id, video_id in targets:
        connection.execute(table.insert().values(id=str(uuid.uuid4()), created_at=now, updated_at=now,
                                                 user_id=user_id, video_id=video_id))


def downgrade():
    op.drop_table("video_stars")
    op.execute("DELETE FROM playback_sessions WHERE user_id IS NULL")
    with op.batch_alter_table("playback_sessions") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
