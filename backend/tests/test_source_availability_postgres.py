from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, select

from app.models import Video
from test_postgres_integration import postgres_workspace, pytestmark
from test_source_availability import (test_authoritative_absence_keeps_archive_stats_and_recovers_with_history as check_recovery,
                                      test_paid_not_found_is_not_deletion_evidence as check_paid)


def test_existing_archives_gain_empty_check_history_without_media_state_changes(postgres_workspace):
    space = postgres_workspace
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with space.engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '02587e9fc064')
        videos = Table('videos', MetaData(), autoload_with=connection, resolve_fks=False)
        now, video_id = datetime.now(timezone.utc), str(uuid4())
        connection.execute(videos.insert().values(id=video_id, bvid='BV1234567890', title='preserved', description='',
            duration=60, source_state='available', source_tags=['kept'], capture_status='complete', metadata_json={},
            created_at=now, updated_at=now))
        command.upgrade(config, 'head')
    with space.sessions() as db:
        video = db.get(Video, video_id)
        assert video.source_availability == {} and video.source_tags == ['kept']
        assert video.capture_status == 'complete' and video.source_state == 'available'


def test_statistics_source_disappearance_and_recovery_are_durable(postgres_workspace, monkeypatch):
    from app.ingest import runner
    monkeypatch.setattr(runner, 'decrypt_secret', lambda value: 'SESSDATA=fixture')
    with postgres_workspace.sessions() as db:
        check_recovery(db, monkeypatch)
    with postgres_workspace.sessions() as db:
        video = db.scalar(select(Video))
        assert video.source_state == 'available'
        assert [row['outcome'] for row in video.source_availability['history']] == ['available', 'unavailable', 'available']


def test_subscription_paid_evidence_uses_postgres_json_semantics(postgres_workspace):
    with postgres_workspace.sessions() as db:
        check_paid(db, 'subscription')
