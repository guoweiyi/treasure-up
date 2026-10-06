"""Small synchronous storage check used by the administrator's connection dialog."""
import hashlib
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from app.config import settings
from app.models import utcnow
from .base import IntegrityError, ObjectMissing
from .service import get_adapter


def browser_delivery_check(adapter, key, version_id, payload):
    """Check response CORS/Range headers using only the disposable probe object.

    This is a server-side check of browser-facing headers, not a claim that a
    remote user's browser has the same network access. No bucket policy changes.
    """
    from app.passkeys import site_policy
    from .delivery import validate_delivery_url
    try:
        origin, _ = site_policy()
        url = validate_delivery_url(adapter.presign(key, 120, version_id=version_id))
        if origin.startswith('https://') and not url.startswith('https://'):
            return 'failed', '站点使用 HTTPS，外网播放地址也需要 HTTPS。'
        with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', url, headers={'Origin': origin, 'Range': 'bytes=17-53'}) as response:
                allowed = response.headers.get('access-control-allow-origin')
                if response.status_code != 206 or not response.headers.get('content-range', '').startswith('bytes 17-53/'):
                    return 'failed', '外网播放地址未正常返回视频范围，请检查域名、访问签名与 Range 支持。'
                # Even a server ignoring Range cannot make us read a whole file.
                returned = next(response.iter_bytes(chunk_size=38), b'')
                if returned != payload[17:54]:
                    return 'failed', '外网播放地址返回的内容不匹配，请检查 CDN 与回源配置。'
                if allowed not in {'*', origin}:
                    return 'failed', f'请在存储桶或 CDN 的 CORS 配置中允许 {origin} 的 GET、HEAD 和 Range 请求。'
        return 'passed', '外网地址的 Range 与跨域响应头正常。'
    except Exception:
        return 'failed', '外网直连检查失败，请检查播放域名、签名和存储桶 CORS 配置。'


def probe_now(profile):
    started = time.monotonic()
    checks = {key: False for key in ("put", "head", "sha256_readback", "single_range", "delete")}
    stage, adapter, created, info = "connect", None, False, None
    key, payload = f"probes/{uuid.uuid4().hex}", uuid.uuid4().bytes * 512
    messages = {
        "connect": "无法连接存储，请检查服务地址与访问凭据。",
        "put": "写入失败，请检查存储桶、目录与写入权限。",
        "head": "对象信息读取失败，请检查读取权限。",
        "sha256_readback": "回读校验失败，请检查读取权限与存储内容。",
        "single_range": "分段读取失败，此位置可能无法正常拖动视频进度。",
        "delete": "测试文件未能清理，请检查删除权限。",
    }
    failure = None
    browser_cors, browser_message = 'not_tested', ''
    try:
        adapter = get_adapter(profile, purpose="probe")
        Path(settings.scratch_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="storage-probe-", dir=settings.scratch_dir) as folder:
            path = Path(folder) / "probe"
            path.write_bytes(payload)
            stage, created = "put", True
            info = adapter.put_file(path, key, "application/octet-stream")
            checks[stage] = True
            if profile.kind != 'local':
                browser_cors, browser_message = browser_delivery_check(adapter, key, info.version_id, payload)
            stage = "head"
            if adapter.head(key, version_id=info.version_id).size != len(payload):
                raise IntegrityError("Probe size mismatch")
            checks[stage] = True
            stage = "sha256_readback"
            with adapter.reader(key, version_id=info.version_id) as stream:
                returned = stream.read(len(payload) + 1)
            if hashlib.sha256(returned).digest() != hashlib.sha256(payload).digest():
                raise IntegrityError("Probe contents mismatch")
            checks[stage] = True
            stage = "single_range"
            if adapter.read_range(key, 17, 53, version_id=info.version_id) != payload[17:54]:
                raise IntegrityError("Probe range mismatch")
            checks[stage] = True
    except Exception:
        failure = stage
    finally:
        if created and adapter is not None:
            try:
                adapter.delete(key, version_id=info.version_id if info else None)
                try:
                    adapter.head(key, version_id=info.version_id if info else None)
                except ObjectMissing:
                    checks["delete"] = True
                else:
                    failure = failure or "delete"
            except Exception:
                failure = failure or "delete"
    passed = all(checks.values())
    return {"status": "passed" if passed else "failed", "provider": profile.kind,
            "checks": checks, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "verified_at": utcnow().isoformat(), "multipart": "not_tested",
            "browser_cors": browser_cors, "browser_message": browser_message,
            "message": "连接正常，读写及分段读取均通过。" if passed else messages[failure or "connect"]}
