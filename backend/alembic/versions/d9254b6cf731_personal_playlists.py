"""Add private named playlists and retain legacy stars in default watch-later."""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from alembic import op
import sqlalchemy as sa

revision = "d9254b6cf731"
down_revision = "c8412d60fa31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("personal_playlists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("system_key", sa.String(30)),
        sa.UniqueConstraint("user_id", "system_key"))
    op.create_index("ix_personal_playlists_user_id", "personal_playlists", ["user_id"])
    op.create_table("personal_playlist_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("playlist_id", sa.String(36), sa.ForeignKey("personal_playlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("note", sa.String(2000), nullable=False),
        sa.Column("watched", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("playlist_id", "video_id"))
    op.create_index("ix_personal_playlist_items_playlist_id", "personal_playlist_items", ["playlist_id"])
    op.create_index("ix_personal_playlist_items_video_id", "personal_playlist_items", ["video_id"])
    op.create_index("ix_personal_playlist_items_order", "personal_playlist_items", ["playlist_id", "created_at", "id"])
    connection = op.get_bind()
    stars = sa.table("video_stars", *[sa.column(key) for key in ("id", "user_id", "video_id", "created_at", "updated_at")])
    playlists = sa.table("personal_playlists", *[sa.column(key) for key in
        ("id", "user_id", "name", "description", "system_key", "created_at", "updated_at")])
    items = sa.table("personal_playlist_items", *[sa.column(key) for key in
        ("id", "playlist_id", "video_id", "note", "watched", "created_at", "updated_at")])
    now = datetime.now(timezone.utc)
    for user_id in connection.scalars(sa.select(stars.c.user_id).distinct()):
        playlist_id = str(uuid5(NAMESPACE_URL, "treasure-up:watch-later:" + user_id))
        connection.execute(playlists.insert().values(id=playlist_id, user_id=user_id, name="稍后看", description="",
            system_key="watch_later", created_at=now, updated_at=now))
        connection.execute(items.insert().from_select(
            ["id", "playlist_id", "video_id", "note", "watched", "created_at", "updated_at"],
            sa.select(stars.c.id, sa.literal(playlist_id), stars.c.video_id, sa.literal(""), sa.literal(False),
                      stars.c.created_at, stars.c.updated_at).where(stars.c.user_id == user_id)))


def downgrade():
    op.drop_table("personal_playlist_items")
    op.drop_table("personal_playlists")
