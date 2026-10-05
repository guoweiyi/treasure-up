"""Query budgets for the real HTTP playback path, with private media fixtures."""
from sqlalchemy import event, select

from app.models import StorageProfile
from app.storage.service import migrate_asset
from test_api import context, login
from test_delivery_api import seed_media


def test_session_lists_complete_routes_and_observations_only_once(context):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    original = db.scalar(select(StorageProfile))
    replica = StorageProfile(name='replica', kind='local', config={'root': str(tmp / 'replica')})
    db.add(replica); db.flush()
    migrate_asset(db, asset.id, replica.id)
    db.commit()
    # HTTP production sessions do not retain the seeding identity map.
    db.expunge(part)
    login(client)
    queries = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith('select'):
            queries.append(statement.lower())

    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', record)
    try:
        response = client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'route_id': original.id})
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert response.status_code == 200, response.text
    # Both replicas contain this fixture's single HLS init/segment asset.
    assert response.json()['selected_route_id'] == original.id
    assert sum('from storage_profiles' in sql and 'asset_locations' in sql for sql in queries) == 1
    assert sum('from storage_observations' in sql for sql in queries) == 1
    assert sum('from video_parts' in sql for sql in queries) == 1
    assert client.post('/api/v1/playback-sessions', json={'part_id': part.id, 'route_id': 'unrelated'}).status_code == 503


def test_healthy_segment_has_no_session_row_lock_or_state_write(context):
    client, db, tmp = context
    _, part, _, asset = seed_media(db, tmp, hls=True)
    login(client)
    created = client.post('/api/v1/playback-sessions', json={'part_id': part.id}).json()
    statements = []

    def record(orm_state):
        statement = orm_state.statement
        statements.append(statement)

    event.listen(db, 'do_orm_execute', record)
    try:
        response = client.get(f'/api/v1/playback-sessions/{created["id"]}/assets/{asset.id}',
                              headers={'Range': 'bytes=13-15'})
    finally:
        event.remove(db, 'do_orm_execute', record)
    assert response.status_code == 206 and response.content == bytes([13, 14, 15])
    assert response.headers['cache-control'].startswith('private')
    assert all(getattr(statement, '_for_update_arg', None) is None for statement in statements)
    assert not db.dirty
