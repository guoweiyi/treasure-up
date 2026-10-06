"""Microsoft Graph drives, using delegated OAuth and resumable uploads."""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import quote, urlparse

import httpx

from .base import IntegrityError, ObjectInfo, ObjectMissing, Storage, StorageError, file_digest, safe_key
from .http_stream import ResponseReader

_tokens = OrderedDict()
_token_lock = threading.Lock()
_DOWNLOAD_DOMAINS = ("sharepoint.com", "sharepoint.cn", "sharepointonline.com", "1drv.com", "1drv.ms", "onedrive.com", "onedrive.live.com", "storage.live.com")


def microsoft_transfer_url(value):
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (ValueError, TypeError):
        raise StorageError("Microsoft 返回的文件传输地址不受信任") from None
    if (parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443)
            or not any(parsed.hostname == suffix or (parsed.hostname or "").endswith("." + suffix) for suffix in _DOWNLOAD_DOMAINS)):
        raise StorageError("Microsoft 返回的文件传输地址不受信任")
    return value


class GraphStorage(Storage):
    def __init__(self, kind, config, credentials, *, purpose="default", on_credentials=None, transport=None):
        self.kind, self.config, self.credentials = kind, dict(config), dict(credentials)
        self.prefix = safe_key(config["prefix"].strip("/")) if config.get("prefix") else ""
        self.drive_id = config.get("drive_id")
        self.graph_origin = "https://microsoftgraph.chinacloudapi.cn" if kind.endswith("_cn") else "https://graph.microsoft.com"
        self.login_origin = "https://login.chinacloudapi.cn" if kind.endswith("_cn") else "https://login.microsoftonline.com"
        self.timeout = httpx.Timeout(5 if purpose in {"probe", "discovery"} else 120, connect=3 if purpose in {"probe", "discovery"} else 10)
        self.on_credentials, self.transport = on_credentials, transport

    def _client(self):
        return httpx.Client(timeout=self.timeout, follow_redirects=False, transport=self.transport, trust_env=False)

    def _token(self):
        tenant = self.config.get("tenant_id") or ("organizations" if self.kind.endswith("_cn") else "common")
        cache_key = hashlib.sha256(json.dumps([self.login_origin, tenant, self.credentials], sort_keys=True).encode()).hexdigest()
        with _token_lock:
            cached = _tokens.get(cache_key)
            if cached and cached[1] > time.monotonic():
                _tokens.move_to_end(cache_key)
                return cached[0]
        data = {"grant_type": "refresh_token", "client_id": self.credentials["client_id"], "refresh_token": self.credentials["refresh_token"],
                "scope": "offline_access " + self.graph_origin + "/.default"}
        if self.credentials.get("client_secret"):
            data["client_secret"] = self.credentials["client_secret"]
        with self._client() as client:
            result = client.post(self.login_origin + "/" + quote(tenant, safe="") + "/oauth2/v2.0/token", data=data)
            if not result.is_success:
                raise StorageError("Microsoft 授权已失效或权限不足，请更新刷新令牌")
            payload = result.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise StorageError("Microsoft 未返回有效访问令牌")
        refresh = payload.get("refresh_token")
        if refresh and refresh != self.credentials["refresh_token"]:
            updated = {**self.credentials, "refresh_token": refresh}
            if self.on_credentials:
                self.on_credentials(updated)
            self.credentials = updated
        with _token_lock:
            cached_value = (token, time.monotonic() + max(0, min(int(payload.get("expires_in", 3600)), 3600) - 120))
            _tokens[cache_key] = cached_value
            updated_key = hashlib.sha256(json.dumps([self.login_origin, tenant, self.credentials], sort_keys=True).encode()).hexdigest()
            _tokens[updated_key] = cached_value
            while len(_tokens) > 128:
                _tokens.popitem(last=False)
        return token

    def _api(self, method, path, **kwargs):
        url = path if path.startswith("https://") else self.graph_origin + "/v1.0" + path
        if not url.startswith(self.graph_origin + "/v1.0/"):
            raise StorageError("Microsoft 分页地址不受信任")
        with self._client() as client:
            response = client.request(method, url, headers={"Authorization": "Bearer " + self._token()}, **kwargs)
        self._check(response)
        return response

    @staticmethod
    def _check(response):
        if response.status_code == 404:
            raise ObjectMissing("Microsoft 云盘中的对象不存在")
        if not response.is_success:
            raise StorageError("Microsoft 云盘请求失败，请检查授权范围、容量和服务状态")

    def _root(self):
        if not self.drive_id:
            raise StorageError("请先选择云盘或文档库")
        return "/drives/" + quote(self.drive_id, safe="")

    def _key(self, key):
        return f"{self.prefix}/{safe_key(key)}" if self.prefix else safe_key(key)

    def _path(self, key):
        return self._root() + "/root:/" + quote(self._key(key), safe="/")

    @staticmethod
    def _version(version_id):
        if version_id:
            raise StorageError("此云盘副本不支持按历史版本读取")

    def head(self, key, *, version_id=None):
        self._version(version_id)
        result = self._api("GET", self._path(key)).json()
        if "file" not in result:
            raise ObjectMissing("云盘路径不是文件")
        return ObjectInfo(int(result["size"]), result.get("eTag"))

    def presign(self, key, expires_in, *, version_id=None):
        self._version(version_id)
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        result = self._api("GET", self._path(key)).json()
        return self._download_url(result)

    def playback_url(self, key, expected_size, expires_in, *, version_id=None):
        """Validate a replica and obtain its URL from one Graph metadata read."""
        self._version(version_id)
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        result = self._api("GET", self._path(key)).json()
        if "file" not in result:
            raise ObjectMissing("云盘路径不是文件")
        if result.get("size") != expected_size:
            raise IntegrityError("云盘媒体大小与已校验副本不一致")
        return self._download_url(result)

    @staticmethod
    def _download_url(result):
        url = result.get("@microsoft.graph.downloadUrl")
        if not url:
            raise StorageError("Microsoft 未返回可直接读取的文件地址")
        # Lifetime is controlled by Microsoft (normally about one hour). Never
        # create a public sharing link or send the OAuth bearer token to a CDN.
        return microsoft_transfer_url(url)

    @contextmanager
    def _download(self, key, headers=None):
        url = self.presign(key, 3600)
        with self._client() as client:
            for _ in range(4):
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = microsoft_transfer_url(response.headers.get("location", ""))
                        continue
                    self._check(response)
                    yield response
                    return
        raise StorageError("Microsoft 文件地址重定向过多")

    @contextmanager
    def reader(self, key, *, version_id=None):
        self._version(version_id)
        with self._download(key) as response:
            yield ResponseReader(response)

    def read_range(self, key, start, end, *, version_id=None):
        self._version(version_id)
        with self._download(key, {"Range": f"bytes={start}-{end}"}) as response:
            if response.status_code != 206 or not response.headers.get("content-range", "").startswith(f"bytes {start}-{end}/"):
                raise StorageError("Microsoft 云盘未按请求返回字节范围")
            return ResponseReader(response).read(end - start + 2)

    def _parents(self, key):
        parent = self._root() + "/root"
        parts = self._key(key).split("/")[:-1]
        for index, name in enumerate(parts):
            path = self._root() + "/root:/" + quote("/".join(parts[:index + 1]), safe="/")
            try:
                item = self._api("GET", path).json()
                if "folder" not in item:
                    raise StorageError("云盘目标目录与已有文件冲突")
            except ObjectMissing:
                endpoint = parent + (":" if "/root:/" in parent else "") + "/children"
                try:
                    self._api("POST", endpoint, json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                except StorageError:
                    # Concurrent uploads may have created this directory.
                    if "folder" not in self._api("GET", path).json():
                        raise
            parent = path

    def put_file(self, path, key, mime_type, *, checkpoint=None, on_checkpoint=None):
        self._parents(key)
        sha, size = file_digest(path)
        if size <= 4 * 1024**2:
            with Path(path).open("rb") as stream:
                self._api("PUT", self._path(key) + ":/content", content=stream.read())
            return self.head(key)
        from app.security import decrypt_secret, encrypt_secret
        state = dict(checkpoint or {})
        if state and (state.get("key"), state.get("sha256"), state.get("size")) != (key, sha, size):
            raise StorageError("云盘续传记录与源文件不一致")
        upload_url, offset = None, 0
        if state.get("upload_url_encrypted"):
            upload_url = microsoft_transfer_url(decrypt_secret(state["upload_url_encrypted"]))
            with self._client() as client:
                result = client.get(upload_url)
            if result.status_code in (404, 410):
                upload_url = None
            else:
                self._check(result)
                offset = self._offset(result.json(), size)
        if not upload_url:
            result = self._api("POST", self._path(key) + ":/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}).json()
            upload_url = microsoft_transfer_url(result["uploadUrl"])
            state = {"key": key, "sha256": sha, "size": size}
        try:
            if on_checkpoint:
                state["upload_url_encrypted"] = encrypt_secret(upload_url)
                on_checkpoint(dict(state))
            with Path(path).open("rb") as stream, self._client() as client:
                stream.seek(offset)
                while offset < size:
                    block = stream.read(10 * 1024**2)  # A multiple of Graph's 320 KiB requirement.
                    if len(block) != min(10 * 1024**2, size - offset):
                        raise StorageError("上传期间源文件长度发生变化")
                    end = offset + len(block)
                    response = client.put(upload_url, content=block, headers={"Content-Length": str(len(block)), "Content-Range": f"bytes {offset}-{end - 1}/{size}"})
                    self._check(response)
                    if end < size and (response.status_code != 202 or self._offset(response.json(), size) != end):
                        raise StorageError("Microsoft 分片确认位置不一致")
                    if end == size and response.status_code not in (200, 201):
                        raise StorageError("Microsoft 尚未完成文件提交")
                    offset = end
                    if on_checkpoint:
                        state["uploaded_bytes"] = offset
                        on_checkpoint(dict(state))
        except Exception:
            if not on_checkpoint:
                with self._client() as client:
                    try:
                        client.delete(upload_url)
                    except httpx.HTTPError:
                        pass
            raise
        return self.head(key)

    @staticmethod
    def _offset(payload, size):
        ranges = payload.get("nextExpectedRanges", [])
        try:
            offset = min(int(value.split("-", 1)[0]) for value in ranges)
        except (ValueError, TypeError):
            raise StorageError("Microsoft 返回了无效的续传位置") from None
        if not 0 <= offset < size or offset % (320 * 1024):
            raise StorageError("Microsoft 返回了不兼容的续传位置")
        return offset

    def delete(self, key, *, version_id=None):
        self._version(version_id)
        try:
            self._api("DELETE", self._path(key))
        except ObjectMissing:
            pass
