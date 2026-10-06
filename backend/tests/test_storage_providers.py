import base64
import hashlib
import hmac
import io
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.storage.base import StorageError
from app.storage.cloud import OssStorage, S3Storage
from app.storage.configuration import KINDS, normalized_config, validate_configuration, validate_credentials
from app.storage.discovery import discover_storage
from app.storage.graph import GraphStorage, _tokens, microsoft_transfer_url
from app.storage.obs import ObsStorage, obs_client
from app.storage.qiniu import QiniuStorage
from app.storage.upyun import UpyunStorage

CREDS = {"access_key_id": "fixture-key", "secret_access_key": "fixture-secret"}
GRAPH_CREDS = {"client_id": "fixture-client", "refresh_token": "fixture-refresh"}


@pytest.mark.parametrize("kind", sorted(KINDS - {"local"}))
def test_provider_configuration_and_credentials(kind):
    if kind.startswith(("onedrive", "sharepoint")):
        config, credentials = {"drive_id": "drive"}, GRAPH_CREDS
    elif kind == "upyun":
        config, credentials = {"bucket": "fixture", "public_base_url": "https://cdn.example.test"}, {"operator": "fixture", "password": "fixture"}
    else:
        config = {"bucket": "fixture", "endpoint": "https://store.example.test"}
        credentials = {"access_key_id": "fixture", "access_key_secret": "fixture"} if kind == "oss" else CREDS
    validate_configuration(kind, config)
    validate_credentials(kind, credentials)
    with pytest.raises(StorageError):
        validate_credentials(kind, {**credentials, "unexpected_password": "secret"})


@pytest.mark.parametrize("key,value", [("private_bucket", "false"), ("delivery_mode", "proxy"), ("public_base_url", "https://user:secret@example.test"), ("prefix", "../escape"), ("read_priority", True)])
def test_invalid_cloud_config_is_rejected(key, value):
    with pytest.raises(StorageError):
        validate_configuration("s3", {"bucket": "fixture", key: value})


def test_s3_cdn_never_rewrites_private_sigv4_host():
    with pytest.raises(StorageError, match="S3"):
        validate_configuration("s3", {"bucket": "fixture", "public_base_url": "https://cdn.example.test", "private_bucket": True})
    client = NS(generate_presigned_url=lambda *a, **kw: "https://signed-api.example.test/file?signature=fixture")
    public = S3Storage({"bucket": "fixture", "prefix": "media", "public_base_url": "https://cdn.example.test", "private_bucket": False}, {}, client=client)
    assert public.presign("videos/稿件-UP/中文.mp4", 60) == "https://cdn.example.test/media/videos/%E7%A8%BF%E4%BB%B6-UP/%E4%B8%AD%E6%96%87.mp4"
    private = S3Storage({"bucket": "fixture"}, {}, client=client)
    assert private.presign("video.mp4", 60).startswith("https://signed-api.example.test/")


def test_oss_custom_domain_signs_with_cname_and_short_probe_timeout():
    adapter = OssStorage({"bucket": "fixture", "region": "cn-hangzhou", "endpoint": "https://oss-cn-hangzhou.aliyuncs.com", "public_base_url": "https://cdn.example.test"},
                         {"access_key_id": "fixture", "access_key_secret": "fixture"}, purpose="probe")
    url = adapter.presign("videos/稿件-UP/video.mp4", 60)
    assert urlparse(url).hostname == "cdn.example.test"
    assert "x-oss-signature" in parse_qs(urlparse(url).query)
    assert adapter.bucket.timeout == (3, 5)


def test_s3_probe_timeouts_and_compatible_checksum_config():
    adapter = S3Storage({"bucket": "fixture", "endpoint": "https://s3.example.test"}, CREDS, purpose="probe")
    config = adapter.client.meta.config
    assert (config.connect_timeout, config.read_timeout, config.retries["total_max_attempts"]) == (3, 5, 1)
    assert config.request_checksum_calculation == "when_required"
    adapter.client.close(); adapter.signing_client.close()


def test_provider_defaults_and_oss_endpoint_region():
    assert normalized_config("oss", {"endpoint": "https://oss-cn-hangzhou-internal.aliyuncs.com"})["region"] == "cn-hangzhou"
    assert normalized_config("r2", {})["region"] == "auto"
    assert normalized_config("minio", {})["addressing_style"] == "path"
    assert normalized_config("cos", {"region": "ap-shanghai"})["endpoint"] == "https://cos.ap-shanghai.myqcloud.com"
    qiniu = normalized_config("qiniu", {"region": "cn-east-1"})
    assert qiniu["region"] == "cn-east-1"
    assert qiniu["endpoint"] == "https://s3.cn-east-1.qiniucs.com"


def test_qiniu_cdn_signature_is_native_and_unicode_encoded(monkeypatch):
    monkeypatch.setattr("app.storage.qiniu.time.time", lambda: 1000)
    adapter = QiniuStorage({"bucket": "fixture", "public_base_url": "https://cdn.example.test", "private_bucket": True}, CREDS, client=object())
    url = adapter.presign("videos/中文.mp4", 60)
    payload, token = url.split("&token=")
    expected = base64.urlsafe_b64encode(hmac.new(b"fixture-secret", payload.encode(), hashlib.sha1).digest()).decode()
    assert token == "fixture-key:" + expected
    assert "e=1060" in url and "%E4%B8%AD" in url


def test_obs_native_tls_and_cname_configuration():
    client = obs_client({"endpoint": "https://obs.cn-north-4.myhuaweicloud.com", "public_base_url": "https://cdn.example.test"}, CREDS, purpose="probe", signing=True)
    try:
        assert client.context.check_hostname and client.context.verify_mode == 2
        assert client.timeout == 5 and client.max_retry_count == 0 and client.is_cname
        assert urlparse(client.createSignedUrl("GET", "fixture", "中文.mp4", expires=60).signedUrl).hostname == "cdn.example.test"
    finally:
        client.close()


def test_obs_native_range_and_metadata_keep_version():
    received = []
    def get(bucket, key, **kwargs):
        received.append(kwargs)
        return NS(status=206, header=[("Content-Range", "bytes 1-3/5")], body=NS(response=io.BytesIO(b"123")))
    client = NS(getObject=get, getObjectMetadata=lambda *a, **k: NS(status=200, body=NS(contentLength=5, etag="e", versionId="v")))
    adapter = ObsStorage({"bucket": "fixture"}, {}, client=client)
    assert adapter.head("file", version_id="v").version_id == "v"
    assert adapter.read_range("file", 1, 3, version_id="v") == b"123"
    assert received[0]["getObjectRequest"].versionId == "v"
    assert received[0]["headers"].range == "bytes=1-3"


def test_upyun_native_auth_streaming_range_and_cdn_token(tmp_path, monkeypatch):
    stored = {}
    def handle(request):
        assert request.headers["authorization"].startswith("UPYUN fixture:")
        uri = request.url.raw_path.decode()
        password = hashlib.md5(b"password").hexdigest().encode()
        expected = base64.b64encode(hmac.new(password, f"{request.method}&{uri}&{request.headers['date']}".encode(), hashlib.sha1).digest()).decode()
        assert request.headers["authorization"] == "UPYUN fixture:" + expected
        if request.method == "PUT":
            stored["bytes"] = request.read()
            return httpx.Response(200)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"x-upyun-file-size": str(len(stored["bytes"]))})
        return httpx.Response(206, headers={"content-range": "bytes 1-3/5"}, content=stored["bytes"][1:4])
    adapter = UpyunStorage({"bucket": "fixture", "public_base_url": "https://cdn.example.test"}, {"operator": "fixture", "password": "password", "token_secret": "cdn-secret"}, transport=httpx.MockTransport(handle))
    source = tmp_path / "source"; source.write_bytes(b"01234")
    assert adapter.put_file(source, "videos/中文.mp4", "video/mp4").size == 5
    assert adapter.read_range("videos/中文.mp4", 1, 3) == b"123"
    monkeypatch.setattr("app.storage.upyun.time.time", lambda: 1000)
    url = adapter.presign("videos/中文.mp4", 60)
    expected = hashlib.md5("cdn-secret&1060&/videos/中文.mp4".encode()).hexdigest()[12:20]
    assert url.endswith("?_upt=" + expected + "1060")


@pytest.mark.parametrize("url", ["http://tenant.sharepoint.com/file", "https://127.0.0.1/file", "https://sharepoint.com.evil.test/file", "https://user:secret@tenant.sharepoint.com/file", "https://tenant.sharepoint.com:444/file"])
def test_graph_never_forwards_secrets_or_bytes_to_untrusted_transfer_hosts(url):
    with pytest.raises(StorageError):
        microsoft_transfer_url(url)


def test_graph_download_uses_preauth_cdn_and_refresh_rotation_cache():
    _tokens.clear()
    updated, calls = [], []
    def handle(request):
        calls.append((request.method, request.url.host))
        if request.url.host == "login.microsoftonline.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"access_token": "access-fixture", "refresh_token": "rotated-fixture", "expires_in": 3600})
        if request.url.host == "graph.microsoft.com":
            assert request.headers["authorization"] == "Bearer access-fixture"
            return httpx.Response(200, json={"size": 5, "file": {}, "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/file?token=fixture"})
        assert "authorization" not in request.headers
        assert request.headers["range"] == "bytes=1-3"
        return httpx.Response(206, headers={"content-range": "bytes 1-3/5"}, content=b"123")
    transport = httpx.MockTransport(handle)
    adapter = GraphStorage("onedrive", {"drive_id": "drive"}, GRAPH_CREDS, on_credentials=updated.append, transport=transport)
    assert adapter.read_range("file", 1, 3) == b"123"
    assert updated[0]["refresh_token"] == "rotated-fixture"
    second = GraphStorage("onedrive", {"drive_id": "drive"}, updated[0], transport=transport)
    assert second.head("file").size == 5
    assert sum(host == "login.microsoftonline.com" for _, host in calls) == 1


def test_graph_china_authority_and_upload_segments(tmp_path, monkeypatch):
    _tokens.clear()
    size = 11 * 1024**2
    source = tmp_path / "source"; source.write_bytes(b"x" * size)
    segments = []
    def handle(request):
        if request.url.host == "login.chinacloudapi.cn":
            return httpx.Response(200, json={"access_token": "cn-fixture", "expires_in": 3600})
        if request.url.host == "microsoftgraph.chinacloudapi.cn":
            if request.url.path.endswith("createUploadSession"):
                return httpx.Response(200, json={"uploadUrl": "https://tenant.sharepoint.cn/upload?secret=fixture"})
            return httpx.Response(200, json={"size": size, "file": {}})
        assert request.url.host == "tenant.sharepoint.cn"
        assert "authorization" not in request.headers
        segments.append(request.headers["content-range"])
        if len(segments) == 1:
            return httpx.Response(202, json={"nextExpectedRanges": [str(10 * 1024**2) + "-"]})
        return httpx.Response(201, json={"size": size})
    adapter = GraphStorage("onedrive_cn", {"drive_id": "drive"}, GRAPH_CREDS, transport=httpx.MockTransport(handle))
    assert adapter.put_file(source, "file.mp4", "video/mp4").size == size
    assert segments == [f"bytes 0-{10 * 1024**2 - 1}/{size}", f"bytes {10 * 1024**2}-{size - 1}/{size}"]


def test_discovery_masks_provider_errors_and_never_returns_keys(monkeypatch):
    class Client:
        def list_buckets(self, **kwargs):
            raise RuntimeError("secret_access_key=DO-NOT-LEAK")
        def close(self):
            pass
    monkeypatch.setattr("app.storage.cloud.S3Storage", lambda *a, **kw: NS(client=Client(), signing_client=Client()))
    with pytest.raises(StorageError) as error:
        discover_storage("s3", {}, CREDS)
    assert "DO-NOT-LEAK" not in str(error.value)


def test_discovery_populates_bucket_region_endpoint_and_bounds_results(monkeypatch):
    class Client:
        def list_buckets(self, **kwargs):
            assert kwargs == {"MaxBuckets": 500}
            return {"Buckets": [{"Name": "fixture", "BucketRegion": "ap-shanghai"}], "ContinuationToken": "more"}
        def close(self):
            pass
    monkeypatch.setattr("app.storage.cloud.S3Storage", lambda *a, **kw: NS(client=Client(), signing_client=Client()))
    value = discover_storage("cos", {"region": "ap-shanghai"}, CREDS)
    assert value["items"] == [{"name": "fixture", "region": "ap-shanghai", "endpoint": "https://cos.ap-shanghai.myqcloud.com"}]
    assert value["truncated"]


def test_obs_multipart_restarts_only_expired_upload_and_checks_real_sdk_models(tmp_path):
    from obs.model import GetResult, InitiateMultipartUploadResponse, ListPartsResponse, Part, GetObjectMetadataResponse
    size = 5 * 1024**2 + 16
    source = tmp_path / "source"; source.write_bytes(b"x" * size)
    states, uploaded = [], []
    class Client:
        def initiateMultipartUpload(self, bucket, key, **kwargs):
            return GetResult(status=200, body=InitiateMultipartUploadResponse(uploadId="new-upload"))
        def listParts(self, bucket, key, upload_id, **kwargs):
            if upload_id == "expired":
                return GetResult(status=404, code="NoSuchUpload")
            assert states[-1]["upload_id"] == "new-upload"
            return GetResult(status=200, body=ListPartsResponse(isTruncated=False, parts=[Part(partNumber=1, size=5 * 1024**2, etag="first")]))
        def uploadPart(self, bucket, key, number, upload_id, **kwargs):
            uploaded.append(number)
            return GetResult(status=200, body=NS(etag="second"))
        def completeMultipartUpload(self, bucket, key, upload_id, body):
            assert [(part.partNum, part.etag) for part in body.parts] == [(1, "first"), (2, "second")]
            return GetResult(status=200)
        def getObjectMetadata(self, *args, **kwargs):
            return GetResult(status=200, body=GetObjectMetadataResponse(contentLength=size, etag="file"))
    adapter = ObsStorage({"bucket": "fixture", "part_size": 5 * 1024**2}, {}, client=Client())
    checkpoint = {"key": "file", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "part_size": 5 * 1024**2, "upload_id": "expired"}
    assert adapter.put_file(source, "file", "video/mp4", checkpoint=checkpoint, on_checkpoint=states.append).size == size
    assert uploaded == [2]


def test_graph_resumes_encrypted_session_without_reuploading_first_fragment(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from app.config import settings
    from app.security import decrypt_secret
    monkeypatch.setattr(settings, "secret_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "secret_key_file", None)
    _tokens.clear()
    size = 11 * 1024**2
    source = tmp_path / "source"; source.write_bytes(b"x" * size)
    states, segments, sessions = [], [], []
    fail = [True]
    upload_url = "https://tenant.sharepoint.com/upload?secret=fixture"
    def handle(request):
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "fixture", "expires_in": 3600})
        if request.url.host == "graph.microsoft.com":
            if request.url.path.endswith("createUploadSession"):
                sessions.append(True)
                return httpx.Response(200, json={"uploadUrl": upload_url})
            return httpx.Response(200, json={"size": size, "file": {}})
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(200, json={"nextExpectedRanges": [str(10 * 1024**2) + "-"]})
        part = request.headers["content-range"]
        if part.startswith("bytes 0-"):
            segments.append(0)
            return httpx.Response(202, json={"nextExpectedRanges": [str(10 * 1024**2) + "-"]})
        if fail[0]:
            fail[0] = False
            raise httpx.ReadTimeout("fixture interrupted")
        segments.append(1)
        return httpx.Response(201, json={"size": size})
    adapter = GraphStorage("onedrive", {"drive_id": "drive"}, GRAPH_CREDS, transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.ReadTimeout):
        adapter.put_file(source, "file.mp4", "video/mp4", on_checkpoint=states.append)
    assert "secret=fixture" not in str(states)
    assert decrypt_secret(states[-1]["upload_url_encrypted"]) == upload_url
    assert adapter.put_file(source, "file.mp4", "video/mp4", checkpoint=states[-1], on_checkpoint=states.append).size == size
    assert segments == [0, 1] and len(sessions) == 1


def test_obs_real_sdk_stops_before_cross_host_redirect_with_credentials_and_body(monkeypatch):
    from obs.client import _RedirectException
    from obs.model import GetResult
    client = obs_client({"endpoint": "https://obs.cn-north-4.myhuaweicloud.com"}, {**CREDS, "session_token": "fixture-sts"})
    sent = []
    def send(server, method, path, headers, entity=None, *args, **kwargs):
        sent.append((server, dict(headers), entity))
        return None
    def redirect(connection):
        raise _RedirectException("fixture redirect", "http://outside.invalid/upload", GetResult(status=307))
    monkeypatch.setattr(client, "_send_request", send)
    try:
        result = client._make_request_with_retry("PUT", "fixture", "video", entity=b"private-video-fixture", parseMethod=redirect)
        assert result.status == 307
        assert len(sent) == 1
        assert sent[0][0] == "fixture.obs.cn-north-4.myhuaweicloud.com"
        assert any(key.lower() == "authorization" for key in sent[0][1])
        assert sent[0][2] == b"private-video-fixture"
    finally:
        client.close()


def test_oss_real_requests_transport_does_not_redirect_sts_or_media_bytes():
    import requests
    from requests.adapters import BaseAdapter
    adapter = OssStorage({"bucket": "fixture", "region": "cn-hangzhou", "endpoint": "https://oss-cn-hangzhou.aliyuncs.com"},
                         {"access_key_id": "fixture", "access_key_secret": "fixture", "security_token": "fixture-sts"})
    sent = []
    class RedirectTransport(BaseAdapter):
        def send(self, request, **kwargs):
            sent.append(request)
            response = requests.Response()
            response.status_code = 307
            response.headers["Location"] = "http://outside.invalid/upload"
            response._content = b""
            response.raw = io.BytesIO(b"")
            response.request = request
            response.url = request.url
            return response
        def close(self):
            pass
    session = adapter.bucket.session.session
    session.mount("https://", RedirectTransport()); session.mount("http://", RedirectTransport())
    try:
        with pytest.raises(Exception):
            adapter.bucket.put_object("file", b"private-video-fixture")
        assert len(sent) == 1
        assert urlparse(sent[0].url).hostname == "fixture.oss-cn-hangzhou.aliyuncs.com"
        assert sent[0].headers.get("x-oss-security-token") == "fixture-sts"
        assert sent[0].body.read() == b"private-video-fixture"
    finally:
        session.close()


@pytest.mark.parametrize("provider", ["s3", "oss"])
@pytest.mark.parametrize("next_marker", [0, -1, 10001, "broken"])
def test_cloud_multipart_cannot_loop_on_nonadvancing_or_invalid_markers(tmp_path, provider, next_marker):
    from test_storage import FakeS3, MultipartOss
    source = tmp_path / "source"; source.write_bytes(b"x" * (5 * 1024**2 + 1))
    if provider == "s3":
        client = FakeS3()
        client.list_parts = lambda **kwargs: {"Parts": [], "IsTruncated": True, "NextPartNumberMarker": next_marker}
        adapter = S3Storage({"bucket": "fixture", "part_size": 5 * 1024**2}, {}, client=client)
    else:
        client = MultipartOss()
        client.list_parts = lambda *args, **kwargs: NS(parts=[], is_truncated=True, next_marker=next_marker)
        adapter = OssStorage({"part_size": 5 * 1024**2}, {}, bucket=client)
    with pytest.raises(StorageError, match="pagination"):
        adapter.put_file(source, "file", "video/mp4", on_checkpoint=lambda state: None)
