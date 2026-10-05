"""Task-local source sharing exercises real storage copying and job completion."""
from pathlib import Path
import os
import shutil
import subprocess
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app import jobs
from app.config import settings
from app.models import Job, MediaVariant, Video, VideoPart
from app.playback import hls, loudness, source
from app.playback.tools import PlaybackError
from app.storage import service
from app.storage.base import file_digest
from test_jobs import job_sessions
from test_postgres_integration import postgres_workspace


def seed(factory, tmp_path, monkeypatch, input_path=None):
    monkeypatch.setattr(settings, 'media_root', tmp_path / 'media')
    monkeypatch.setattr(settings, 'scratch_dir', tmp_path / 'scratch')
    if input_path is None:
        input_path = tmp_path / 'input.mp4'
        input_path.write_bytes(b'original fixture bytes')
    with factory() as db:
        asset = service.ingest_file(db, input_path, kind='media', mime_type='video/mp4')
        video = Video(bvid='BVSourceScope'); db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid='source-scope', duration=2); db.add(part); db.flush()
        variant = MediaVariant(part_id=part.id, asset_id=asset.id, kind='archive', format_key='fixture',
                               duration=2, video_codec='h264', audio_codec='aac')
        db.add(variant); db.commit()
        return variant.id, asset.id, input_path


def prepare(factory, variant_id):
    with factory() as db:
        job = jobs.enqueue(db, 'prepare_media', variant_id, dedupe_key=f'source-test:{uuid4()}',
                           policy={'analyze_loudness': True, 'package': True, 'segment_seconds': 6})
        db.commit()
        return job.id


def track_copies(monkeypatch):
    copies, paths = [], []
    copy = service.copy_asset_to
    materialize = source.materialize_asset

    def count_copy(db, asset_id, target):
        copies.append(asset_id)
        return copy(db, asset_id, target)

    def remember_path(db, asset_id, target):
        result = materialize(db, asset_id, target)
        paths.append(result)
        return result

    monkeypatch.setattr(service, 'copy_asset_to', count_copy)
    monkeypatch.setattr(source, 'materialize_asset', remember_path)
    return copies, paths


@pytest.mark.parametrize('failure', [None, 'error', 'cancel'])
def test_prepare_shares_one_verified_copy_and_cleans_on_all_exits(job_sessions, tmp_path, monkeypatch, failure):
    variant_id, asset_id, original = seed(job_sessions, tmp_path, monkeypatch)
    before = file_digest(original)
    copies, paths = track_copies(monkeypatch)
    inspected = []
    streams = [{'codec_type': 'video', 'index': 0, 'codec_name': 'h264'},
               {'codec_type': 'audio', 'index': 1, 'codec_name': 'aac', 'channels': 2}]

    def probe(path, **kwargs):
        if path.name == 'source.media':
            inspected.append(path)
            assert file_digest(path) == before
        return {'streams': streams, 'format': {'duration': 2}}

    def package(arguments, **kwargs):
        if failure == 'error':
            raise PlaybackError('Synthetic packaging failure')
        if failure == 'cancel':
            raise jobs.LeaseLost('Synthetic revoked lease')
        folder = Path(arguments[-1]).parent
        (folder / 'init.mp4').write_bytes(b'fixture init')
        (folder / 'segment_000000.m4s').write_bytes(b'fixture segment')
        (folder / 'index.m3u8').write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2,\nsegment_000000.m4s\n#EXT-X-ENDLIST\n')

    monkeypatch.setattr(hls, 'probe_media', probe)
    monkeypatch.setattr(loudness, 'probe_media', probe)
    monkeypatch.setattr(loudness, 'run_tool', lambda *a, **k: (b'', b'{"input_i":-16,"input_tp":-2}'))
    monkeypatch.setattr(hls, 'run_tool', package)
    outcome = jobs.run_job_id(prepare(job_sessions, variant_id))
    assert outcome['status'] == ('lease_lost' if failure == 'cancel' else 'failed' if failure else 'succeeded')
    assert copies == [asset_id]
    assert len(inspected) == 2 and inspected[0] == inspected[1] == paths[0]
    assert not paths[0].exists() and not list(settings.scratch_dir.glob('playback-source-*'))
    assert not list(settings.scratch_dir.glob('hls-*'))
    assert file_digest(original) == before
    if not failure:
        # Both completed operations reuse their persistent results before asking
        # the new scope for a source: no second copy, verification or temp folder.
        assert jobs.run_job_id(prepare(job_sessions, variant_id))['status'] == 'succeeded'
        assert copies == [asset_id]
        assert len(inspected) == 2
    else:
        with job_sessions() as db:
            assert not db.scalar(select(MediaVariant).where(MediaVariant.kind == 'hls'))


def test_scope_rejects_other_asset_changed_source_and_use_after_cleanup(job_sessions, tmp_path, monkeypatch):
    _, asset_id, original = seed(job_sessions, tmp_path, monkeypatch)
    with job_sessions() as db:
        other_file = tmp_path / 'other.mp4'; other_file.write_bytes(b'other media')
        other = service.ingest_file(db, other_file, kind='media', mime_type='video/mp4'); db.commit()
        with source.VerifiedSource(db) as provider:
            path = provider(asset_id)
            with pytest.raises(PlaybackError, match='identity'):
                provider(other.id)
            path.chmod(0o600); path.write_bytes(b'changed source')
            with pytest.raises(PlaybackError, match='changed'):
                provider(asset_id)
        assert not path.exists()
        with pytest.raises(PlaybackError, match='not active'):
            provider(asset_id)
    assert original.read_bytes() == b'original fixture bytes'


@pytest.mark.skipif(not shutil.which(str(settings.ffmpeg_path)) or not shutil.which(str(settings.ffprobe_path)),
                    reason='FFmpeg integration tools unavailable')
def test_real_prepare_job_analyzes_and_packages_from_one_verified_copy(job_sessions, tmp_path, monkeypatch):
    original = tmp_path / 'synthetic.mp4'
    subprocess.run([str(settings.ffmpeg_path), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000', '-t', '2', '-c:v', 'libx264',
                    '-c:a', 'aac', '-movflags', '+faststart', str(original)], check=True, capture_output=True)
    variant_id, asset_id, _ = seed(job_sessions, tmp_path, monkeypatch, original)
    before = file_digest(original)
    copies, paths = track_copies(monkeypatch)
    outcome = jobs.run_job_id(prepare(job_sessions, variant_id))
    assert outcome['status'] == 'succeeded'
    with job_sessions() as db:
        variant = db.get(MediaVariant, variant_id)
        assert variant.metadata_json['loudness']['status'] == 'ready'
        packaged = db.scalar(select(MediaVariant).where(MediaVariant.kind == 'hls'))
        assert packaged and hls.load_hls_index(db, packaged.asset_id)['source_asset_id'] == asset_id
    assert copies == [asset_id] and len(paths) == 1
    assert not paths[0].exists() and file_digest(original) == before
    assert jobs.run_job_id(prepare(job_sessions, variant_id))['status'] == 'succeeded'
    assert copies == [asset_id]


def test_packaging_failure_commits_analysis_and_retry_does_not_measure_again(job_sessions, tmp_path, monkeypatch):
    import app.playback as playback
    variant_id, asset_id, _ = seed(job_sessions, tmp_path, monkeypatch)
    copies, paths = track_copies(monkeypatch)
    measurements, packages = [], []
    monkeypatch.setattr(loudness, 'probe_media', lambda *a, **k: {'streams': [
        {'codec_type': 'audio', 'index': 1, 'codec_name': 'flac', 'channels': 2}]})
    def measure(*args, **kwargs):
        measurements.append(True)
        return b'', b'{"input_i":-16,"input_tp":-2}'
    def package(db, target, *, source_provider, **kwargs):
        packages.append(source_provider(asset_id))
        if len(packages) == 1:
            raise PlaybackError('Synthetic packaging failure after successful analysis')
        return SimpleNamespace(id='fixture-hls')
    monkeypatch.setattr(loudness, 'run_tool', measure)
    monkeypatch.setattr(playback, 'package_variant', package)
    job_id = prepare(job_sessions, variant_id)
    assert jobs.run_job_id(job_id)['status'] == 'failed'
    with job_sessions() as db:
        measurement = db.get(MediaVariant, variant_id).metadata_json['loudness']
        assert measurement['status'] == 'ready'
        assert measurement['source_asset_id'] == asset_id
        assert db.get(Job, job_id).result['loudness'] == measurement
        # Emulate the authorized retry of this same failed job, not a new job.
        job = db.get(Job, job_id); job.status = 'queued'; db.commit()
    assert jobs.run_job_id(job_id)['status'] == 'succeeded'
    assert measurements == [True]
    assert copies == [asset_id, asset_id]  # one verified source per attempt
    assert all(not path.exists() for path in paths)


@pytest.mark.skipif(os.environ.get('TREASURE_RUN_POSTGRES_TESTS') != '1', reason='Explicit PostgreSQL integration required')
@pytest.mark.parametrize('revocation', ['cancel', 'new_owner'])
def test_loudness_stage_commit_rejects_revocation_after_analysis_flush(postgres_workspace, tmp_path, monkeypatch, revocation):
    import app.playback as playback
    factory = postgres_workspace.sessions
    variant_id, _, _ = seed(factory, tmp_path, monkeypatch)
    job_id = prepare(factory, variant_id)
    monkeypatch.setattr(loudness, 'probe_media', lambda *a, **k: {'streams': [
        {'codec_type': 'audio', 'index': 1, 'codec_name': 'flac', 'channels': 2}]})
    monkeypatch.setattr(loudness, 'run_tool', lambda *a, **k: (b'', b'{"input_i":-16,"input_tp":-2}'))
    analyze = playback.analyze_variant
    def analyze_then_revoke(db, target, **kwargs):
        result = analyze(db, target, **kwargs)  # real metadata write has flushed
        with factory() as other:
            other.execute(update(Job).where(Job.id == job_id).values(
                **({'status': 'cancelled'} if revocation == 'cancel' else {'lease_owner': 'new-worker'})))
            other.commit()
        return result
    monkeypatch.setattr(playback, 'analyze_variant', analyze_then_revoke)
    monkeypatch.setattr(playback, 'package_variant', lambda *a, **k: pytest.fail('Cancelled analysis must not reach packaging'))
    assert jobs.run_job_id(job_id)['status'] == ('cancelled' if revocation == 'cancel' else 'lease_lost')
    with factory() as db:
        assert 'loudness' not in (db.get(MediaVariant, variant_id).metadata_json or {})
        job = db.get(Job, job_id)
        assert job.status == ('cancelled' if revocation == 'cancel' else 'running')
        if revocation == 'new_owner': assert job.lease_owner == 'new-worker'
    assert not list(settings.scratch_dir.glob('playback-source-*'))
