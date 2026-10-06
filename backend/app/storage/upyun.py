"""UpYun REST storage and CDN token authentication."""
import base64
from contextlib import contextmanager
from email.utils import formatdate
import hashlib
import hmac
from pathlib import Path
import time
from urllib.parse import quote

import httpx

from .base import ObjectInfo, ObjectMissing, Storage, StorageError, safe_key
from .cloud import _endpoint, public_object_url
from .http_stream import ResponseReader


class UpyunStorage(Storage):
    kind = "upyun"

    def __init__(self, config, credentials, *, purpose="default", transport=None):
        self.config, self.credentials = dict(config), credentials
        self.bucket = config["bucket"]
        self.prefix = safe_key(config["prefix"].strip("/")) if config.get("prefix") else ""
        self.endpoint = _endpoint(config.get("endpoint") or "https://v0.api.upyun.com").rstrip("/")
        self.timeout = httpx.Timeout(5 if purpose in {"probe", "discovery"} else 120, connect=3 if purpose in {"probe", "discovery"} else 10)
        self.transport = transport

    def _key(self, key):
        return f"{self.prefix}/{safe_key(key)}" if self.prefix else safe_key(key)

    def _headers(self, method, uri, headers=None):
        date = formatdate(usegmt=True)
        password = hashlib.md5(self.credentials["password"].encode()).hexdigest().encode()
        signature = base64.b64encode(hmac.new(password, f"{method}&{uri}&{date}".encode(), hashlib.sha1).digest()).decode()
        return {"Date": date, "Authorization": f"UPYUN {self.credentials['operator']}:{signature}", **(headers or {})}

    @contextmanager
    def _request(self, method, key, *, headers=None, content=None):
        uri = "/" + quote(self.bucket, safe="") + "/" + quote(self._key(key), safe="/")
        with httpx.Client(timeout=self.timeout, follow_redirects=False, transport=self.transport, trust_env=False) as client:
            with client.stream(method, self.endpoint + uri, headers=self._headers(method, uri, headers), content=content) as response:
                if response.status_code == 404:
                    raise ObjectMissing("又拍云对象不存在")
                if not response.is_success:
                    raise StorageError("又拍云请求失败，请检查操作员权限和服务名称")
                yield response

    def head(self, key, *, version_id=None):
        self._version(version_id)
        with self._request("HEAD", key) as response:
            return ObjectInfo(int(response.headers.get("x-upyun-file-size") or response.headers["content-length"]), response.headers.get("etag"))

    @staticmethod
    def _version(version_id):
        if version_id:
            raise StorageError("又拍云不支持按对象版本读取")

    @contextmanager
    def reader(self, key, *, version_id=None):
        self._version(version_id)
        with self._request("GET", key) as response:
            yield ResponseReader(response)

    def read_range(self, key, start, end, *, version_id=None):
        self._version(version_id)
        with self._request("GET", key, headers={"Range": f"bytes={start}-{end}"}) as response:
            if response.status_code != 206 or not response.headers.get("content-range", "").startswith(f"bytes {start}-{end}/"):
                raise StorageError("又拍云未按请求返回字节范围")
            return ResponseReader(response).read(end - start + 2)

    def put_file(self, path, key, mime_type, *, checkpoint=None, on_checkpoint=None):
        # REST upload streams the file without loading video bytes into memory.
        # UpYun REST does not share the S3 multipart/checkpoint protocol.
        with Path(path).open("rb") as stream:
            chunks = iter(lambda: stream.read(1024 * 1024), b"")
            with self._request("PUT", key, headers={"Content-Type": mime_type, "Content-Length": str(Path(path).stat().st_size), "mkdir": "true"}, content=chunks):
                pass
        return self.head(key)

    def presign(self, key, expires_in, *, version_id=None):
        self._version(version_id)
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        if not self.config.get("public_base_url"):
            raise StorageError("请先配置又拍云访问域名")
        path = "/" + self._key(key)
        url = public_object_url(self.config["public_base_url"], self._key(key))
        if self.credentials.get("token_secret"):
            expires = str(int(time.time()) + expires_in)
            token = hashlib.md5(f"{self.credentials['token_secret']}&{expires}&{path}".encode()).hexdigest()[12:20]
            url += "?_upt=" + token + expires
        elif self.config.get("private_bucket", True):
            raise StorageError("又拍云私有访问请填写 CDN Token 密钥，或明确选择公开读取")
        return url

    def delete(self, key, *, version_id=None):
        self._version(version_id)
        try:
            with self._request("DELETE", key):
                pass
        except ObjectMissing:
            pass
