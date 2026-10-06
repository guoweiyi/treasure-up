"""Huawei OBS uses its native API/signatures, not an S3 compatibility assumption."""
from contextlib import contextmanager
import math
from pathlib import Path

from .base import ObjectInfo, ObjectMissing, Storage, StorageError, file_digest, safe_key
from .cloud import _endpoint, public_object_url


def checked(response):
    if response.status == 404:
        raise ObjectMissing("OBS 对象不存在")
    if not 200 <= response.status < 300:
        raise StorageError("OBS 请求失败，请检查区域、访问权限和服务状态")
    return response.body


def obs_client(config, credentials, *, purpose="default", signing=False):
    from obs import ObsClient
    import certifi
    quick = purpose in {"probe", "discovery"}
    cname = signing and bool(config.get("public_base_url"))
    endpoint = (config.get("public_base_url") or config.get("public_endpoint")) if signing else None
    client = ObsClient(access_key_id=credentials["access_key_id"], secret_access_key=credentials["secret_access_key"],
                     security_token=credentials.get("session_token"), server=_endpoint(endpoint or config["endpoint"]),
                     timeout=5 if quick else 120, max_retry_count=0 if quick else 3, max_redirect_count=1,
                     ssl_verify=certifi.where(), is_signature_negotiation=False, is_cname=cname)
    if getattr(client, "context", None) is not None:
        # The vendor SDK verifies the CA but disables hostname matching by
        # default; both checks are required for authenticated storage traffic.
        client.context.check_hostname = True
    return client


class ObsStorage(Storage):
    kind = "obs"

    def __init__(self, config, credentials, *, purpose="default", client=None, signing_client=None):
        self.config, self.bucket = dict(config), config["bucket"]
        self.prefix = safe_key(config["prefix"].strip("/")) if config.get("prefix") else ""
        self.part_size = max(5 * 1024**2, int(config.get("part_size", 16 * 1024**2)))
        self.client = client or obs_client(config, credentials, purpose=purpose)
        self.signing_client = signing_client or (self.client if client else obs_client(config, credentials, purpose=purpose, signing=True))

    def _key(self, key):
        return f"{self.prefix}/{safe_key(key)}" if self.prefix else safe_key(key)

    def head(self, key, *, version_id=None):
        result = checked(self.client.getObjectMetadata(self.bucket, self._key(key), versionId=version_id))
        return ObjectInfo(int(result.contentLength), result.etag, result.versionId)

    @contextmanager
    def reader(self, key, *, version_id=None):
        from obs import GetObjectRequest
        result = checked(self.client.getObject(self.bucket, self._key(key), getObjectRequest=GetObjectRequest(versionId=version_id)))
        try:
            yield result.response
        finally:
            result.response.close()

    def read_range(self, key, start, end, *, version_id=None):
        from obs import GetObjectHeader, GetObjectRequest
        result = self.client.getObject(self.bucket, self._key(key), getObjectRequest=GetObjectRequest(versionId=version_id),
                                       headers=GetObjectHeader(range=f"bytes={start}-{end}"))
        body = checked(result)
        try:
            headers = {k.lower(): str(v) for k, v in (result.header or [])}
            if result.status != 206 or not headers.get("content-range", "").startswith(f"bytes {start}-{end}/"):
                raise StorageError("OBS 未按请求返回字节范围")
            return body.response.read(end - start + 2)
        finally:
            body.response.close()

    def put_file(self, path, key, mime_type, *, checkpoint=None, on_checkpoint=None):
        from obs import CompleteMultipartUploadRequest, CompletePart, PutObjectHeader
        sha, size = file_digest(path)
        object_key = self._key(key)
        if size < self.part_size:
            checked(self.client.putFile(self.bucket, object_key, str(path), metadata={"sha256": sha}, headers=PutObjectHeader(contentType=mime_type)))
            return self.head(key)
        part_size = max(self.part_size, math.ceil(size / 10000))
        state = dict(checkpoint or {})
        if state and (state.get("key"), state.get("sha256"), state.get("part_size")) != (key, sha, part_size):
            raise StorageError("分片续传记录与源文件不一致")
        may_restart = bool(state)
        def initiate():
            result = checked(self.client.initiateMultipartUpload(self.bucket, object_key, metadata={"sha256": sha}, contentType=mime_type))
            return {"key": key, "sha256": sha, "part_size": part_size, "upload_id": result.uploadId}
        if not state:
            state = initiate()
        try:
            if on_checkpoint:
                on_checkpoint(dict(state))
            existing, marker = {}, 0
            while True:
                response = self.client.listParts(self.bucket, object_key, state["upload_id"], maxParts=1000, partNumberMarker=marker)
                if may_restart and response.status == 404 and response.errorCode == "NoSuchUpload":
                    may_restart = False
                    state, existing, marker = initiate(), {}, 0
                    if on_checkpoint:
                        on_checkpoint(dict(state))
                    continue
                page = checked(response)
                existing.update({p.partNumber: p for p in (page.parts or [])})
                if not page.isTruncated:
                    break
                if not page.nextPartNumberMarker or page.nextPartNumberMarker <= marker:
                    raise StorageError("OBS 分片分页未推进")
                marker = page.nextPartNumberMarker
            completed = []
            with Path(path).open("rb") as stream:
                number = 1
                while block := stream.read(part_size):
                    prior = existing.get(number)
                    etag = prior.etag if prior and prior.size == len(block) else checked(
                        self.client.uploadPart(self.bucket, object_key, number, state["upload_id"], content=block)).etag
                    completed.append(CompletePart(partNum=number, etag=etag))
                    if on_checkpoint:
                        state["completed_parts"] = number
                        on_checkpoint(dict(state))
                    number += 1
            checked(self.client.completeMultipartUpload(self.bucket, object_key, state["upload_id"], CompleteMultipartUploadRequest(parts=completed)))
        except Exception:
            if not on_checkpoint:
                self.client.abortMultipartUpload(self.bucket, object_key, state["upload_id"])
            raise
        return self.head(key)

    def presign(self, key, expires_in, *, version_id=None):
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        if not self.config.get("private_bucket", True) and self.config.get("public_base_url"):
            return public_object_url(self.config["public_base_url"], self._key(key), version_id)
        return self.signing_client.createSignedUrl("GET", self.bucket, self._key(key), expires=expires_in,
                                                   queryParams={"versionId": version_id} if version_id else None).signedUrl

    def delete(self, key, *, version_id=None):
        response = self.client.deleteObject(self.bucket, self._key(key), versionId=version_id)
        if response.status != 404:
            checked(response)
