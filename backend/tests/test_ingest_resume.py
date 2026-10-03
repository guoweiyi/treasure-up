from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_dlp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest import media
from app.ingest.errors import IngestError
from app.models import Base, Video, VideoPart


def test_interrupted_long_media_keeps_resume_path_without_signed_url_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scratch_dir", tmp_path / "scratch")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    monkeypatch.setattr(media, "_probe", lambda path: ({"codec_name": "h264", "width": 1920, "height": 1080}, {"codec_name": "aac"}, 7200))
    calls, options_seen = [], []
    class Downloader:
        def __init__(self, options):
            options_seen.append(options)
            self.params = {**options, "outtmpl": {"default": options["outtmpl"]}}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build_format_selector(self, expression): return lambda context: []
        def extract_info(self, url, download=False):
            return {"id": "BV1234567890", "format_id": "80", "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "width": 1920, "height": 1080,
                    "url": "https://cdn.invalid/video?signature=" + str(len(calls))}
        def process_info(self, info):
            target = Path(self.params["outtmpl"]["default"].replace("%(ext)s", "mp4"))
            partial = Path(str(target) + ".part")
            calls.append(target)
            if len(calls) == 1:
                partial.write_bytes(b"resumable")
                raise IngestError("模拟断线", code="network_error")
            assert partial.read_bytes() == b"resumable"
            partial.rename(target)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloader)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        video = Video(bvid="BV1234567890", aid="123")
        db.add(video); db.flush()
        part = VideoPart(video_id=video.id, cid="11", position=1, duration=7200)
        db.add(part); db.flush()
        source = SimpleNamespace(cookies=[], view=lambda bvid: {"pages": [{"cid": "11", "page": 1}]})
        policy = {"fragment_concurrency": 1, "download_rate_bytes": 2_000_000}
        with pytest.raises(IngestError, match="断线"):
            media.archive_media(db, source, video, part, policy)
        variant, reused = media.archive_media(db, source, video, part, policy)
        assert calls[0] == calls[1] and not reused and variant.duration == 7200
        assert options_seen[0]["continuedl"] is True
        assert options_seen[0]["concurrent_fragment_downloads"] == 1
        assert options_seen[0]["skip_unavailable_fragments"] is False
        assert options_seen[0]["ratelimit"] == 2_000_000
        assert "signature" not in variant.format_key
    engine.dispose()
