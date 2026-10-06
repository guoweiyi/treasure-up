from datetime import timedelta

from sqlalchemy import select

from app import catalog
from app.models import Comment
from test_api import context, seed_catalog
from test_catalog_batch import library, queries, NOW


def test_comment_previews_are_top_three_with_local_count_and_flat_reply_pages(context):
    client, db, _ = context
    video, *_ = seed_catalog(db)
    parent = Comment(video_id=video.id, rpid='preview-root', content='parent', reply_count=99)
    db.add(parent)
    for index, likes in enumerate([2, 80, 50, 80, 5]):
        db.add(Comment(id=f'preview-{index}', video_id=video.id, rpid=f'reply-{index}',
                       root_rpid='preview-root', parent_rpid='reply-1' if index == 3 else 'preview-root',
                       content=f'child {index}', like_count=likes, posted_at=NOW + timedelta(seconds=index)))
    db.commit()
    url = f'/api/v1/videos/{video.id}/comments'
    parent_row = next(item for item in client.get(url).json()['items'] if item['id'] == parent.id)
    assert parent_row['reply_count'] == 99 and parent_row['saved_reply_count'] == 5
    assert [item['id'] for item in parent_row['preview_replies']] == ['preview-3', 'preview-1', 'preview-2']
    children = client.get(url, params={'root': 'preview-root', 'page_size': 2}).json()
    assert children['total'] == 5
    assert [item['id'] for item in children['items']] == ['preview-3', 'preview-1']
    assert all('preview_replies' not in item for item in children['items'])
    # Search returns matching children independently, without pulling in unrelated threads.
    found = client.get(url, params={'q': 'child 3'}).json()['items']
    assert len(found) == 1 and found[0]['id'] == 'preview-3' and 'preview_replies' not in found[0]


def test_preview_queries_are_bounded_and_preserve_authors_assets_and_video_scope(library):
    engine, sessions = library
    with sessions() as db:
        roots = []
        for index in range(24):
            key = f'{index:03d}'
            roots.append(Comment(id=f'root-{key}', video_id=f'video-{key}', rpid='12', root_rpid='12', content='root'))
            # Existing child has archived author/avatar/assets and belongs only to this video.
        db.add_all(roots)
        db.commit()
        for size in (1, 24):
            with queries(engine) as statements:
                result = catalog.comment_views(db, roots[:size], include_previews=True)
            assert len(statements) == 6
            for index, row in enumerate(result):
                child = row['preview_replies'][0]
                assert row['saved_reply_count'] == 1
                assert child['id'] == f'comment-{index:03d}'
                assert child['author']['name'] == f'archived {index}'
                assert child['is_uploader'] is True
                assert child['images'] == ['/api/v1/assets/image-first', '/api/v1/assets/image-last']
        empty = Comment(video_id='video-000', rpid='empty-root', content='empty')
        db.add(empty); db.flush()
        result = catalog.comment_views(db, [empty], include_previews=True)[0]
        assert result['preview_replies'] == [] and result['saved_reply_count'] == 0
