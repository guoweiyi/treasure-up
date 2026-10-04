from __future__ import annotations

import math
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from .base import ObjectInfo, ObjectMissing, Storage, StorageError, file_digest, safe_key


def _endpoint(value: str | None):
    if value is not None:
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError:
            raise StorageError("Endpoint must be an HTTP(S) origin without credentials") from None
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or (port is not None and not 1 <= port <= 65535):
            raise StorageError("Endpoint must be an HTTP(S) origin without credentials")
    return value


class S3Storage(Storage):
    kind = "s3"

    def __init__(self, config: dict, credentials: dict, *, client=None, signing_client=None):
        self.bucket = config.get("bucket")
        if not self.bucket:
            raise StorageError("S3 bucket is required")
        self.prefix = safe_key(config["prefix"].strip("/")) if config.get("prefix") else ""
        self.part_size = max(5 * 1024**2, int(config.get("part_size", 16 * 1024**2)))
        if client is None:
            import boto3
            from botocore.config import Config
            if not credentials.get("access_key_id") or not credentials.get("secret_access_key"):
                raise StorageError("Explicit S3 credentials are required")
            options = dict(region_name=config.get("region", "us-east-1"), aws_access_key_id=credentials["access_key_id"], aws_secret_access_key=credentials["secret_access_key"], aws_session_token=credentials.get("session_token"), config=Config(signature_version="s3v4", s3={"addressing_style": config.get("addressing_style", "virtual")}, connect_timeout=10, read_timeout=120, retries={"max_attempts": 3}))
            client = boto3.client("s3", endpoint_url=_endpoint(config.get("endpoint")), **options)
            signing_client = boto3.client("s3", endpoint_url=_endpoint(config.get("public_endpoint") or config.get("endpoint")), **options)
        self.client = client
        self.signing_client = signing_client or client

    def _key(self, key):
        return f"{self.prefix}/{safe_key(key)}" if self.prefix else safe_key(key)

    def _args(self, key, version_id=None):
        args = {"Bucket": self.bucket, "Key": self._key(key)}
        if version_id:
            args["VersionId"] = version_id
        return args

    def head(self, key, *, version_id=None):
        try:
            result = self.client.head_object(**self._args(key, version_id))
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if str(code) in ("404", "NoSuchKey", "NotFound", "NoSuchVersion"):
                raise ObjectMissing("Object does not exist") from exc
            raise StorageError("S3 metadata request failed") from exc
        return ObjectInfo(int(result["ContentLength"]), result.get("ETag"), result.get("VersionId"), result.get("ChecksumSHA256"))

    @contextmanager
    def reader(self, key, *, version_id=None):
        stream = self.client.get_object(**self._args(key, version_id))["Body"]
        try:
            yield stream
        finally:
            stream.close()

    def put_file(self, path, key, mime_type, *, checkpoint=None, on_checkpoint=None):
        sha, size = file_digest(path)
        args = self._args(key)
        if size < self.part_size:
            with Path(path).open("rb") as stream:
                self.client.put_object(**args, Body=stream, ContentType=mime_type, Metadata={"sha256": sha})
            return self.head(key)
        part_size = max(self.part_size, math.ceil(size / 10000))
        state = dict(checkpoint or {})
        if state and (state.get("key"), state.get("sha256"), state.get("part_size")) != (key, sha, part_size):
            raise StorageError("Multipart checkpoint does not match source")
        if not state:
            result = self.client.create_multipart_upload(**args, ContentType=mime_type, Metadata={"sha256": sha})
            state = {"key": key, "sha256": sha, "part_size": part_size, "upload_id": result["UploadId"]}
        upload_args = {**args, "UploadId": state["upload_id"]}
        try:
            if on_checkpoint:
                on_checkpoint(dict(state))
            existing, marker = {}, None
            while True:
                page = self.client.list_parts(**upload_args, **({"PartNumberMarker": marker} if marker else {}))
                existing.update({p["PartNumber"]: p for p in page.get("Parts", [])})
                if not page.get("IsTruncated"):
                    break
                marker = page["NextPartNumberMarker"]
            parts = []
            with Path(path).open("rb") as stream:
                number = 1
                while block := stream.read(part_size):
                    prior = existing.get(number)
                    if prior and prior["Size"] == len(block):
                        etag = prior["ETag"]
                    else:
                        etag = self.client.upload_part(**upload_args, PartNumber=number, Body=block)["ETag"]
                    parts.append({"PartNumber": number, "ETag": etag})
                    if on_checkpoint:
                        state["completed_parts"] = number
                        on_checkpoint(dict(state))
                    number += 1
            self.client.complete_multipart_upload(**upload_args, MultipartUpload={"Parts": parts})
        except Exception:
            if not on_checkpoint:
                self.client.abort_multipart_upload(**upload_args)
            raise
        return self.head(key)

    def presign(self, key, expires_in, *, version_id=None):
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        return self.signing_client.generate_presigned_url("get_object", Params=self._args(key, version_id), ExpiresIn=expires_in)

    def read_range(self, key, start, end, *, version_id=None):
        result = self.client.get_object(**self._args(key, version_id), Range=f"bytes={start}-{end}")
        stream = result["Body"]
        try:
            if result.get("ResponseMetadata", {}).get("HTTPStatusCode") != 206 or not result.get("ContentRange", "").startswith(f"bytes {start}-{end}/"):
                raise StorageError("S3 did not honor byte range")
            return stream.read(end - start + 2)
        finally:
            stream.close()

    def delete(self, key, *, version_id=None):
        self.client.delete_object(**self._args(key, version_id))


class OssStorage(Storage):
    kind = "oss"

    def __init__(self, config: dict, credentials: dict, *, bucket=None, signing_bucket=None):
        self.prefix = safe_key(config["prefix"].strip("/")) if config.get("prefix") else ""
        self.part_size = max(1024 * 1024, int(config.get("part_size", 16 * 1024**2)))
        if bucket is None:
            import oss2
            from oss2.credentials import StaticCredentialsProvider
            if not config.get("bucket") or not config.get("region") or not config.get("endpoint"):
                raise StorageError("OSS bucket, region and endpoint are required")
            if not credentials.get("access_key_id") or not credentials.get("access_key_secret"):
                raise StorageError("Explicit OSS credentials are required")
            auth = oss2.ProviderAuthV4(StaticCredentialsProvider(credentials["access_key_id"], credentials["access_key_secret"], credentials.get("security_token")))
            bucket = oss2.Bucket(auth, _endpoint(config["endpoint"]), config["bucket"], region=config["region"], connect_timeout=10)
            signing_bucket = oss2.Bucket(auth, _endpoint(config.get("public_endpoint") or config["endpoint"]), config["bucket"], region=config["region"], connect_timeout=10)
        self.bucket = bucket
        self.signing_bucket = signing_bucket or bucket

    def _key(self, key):
        return f"{self.prefix}/{safe_key(key)}" if self.prefix else safe_key(key)

    def _params(self, version_id):
        return {"versionId": version_id} if version_id else None

    def head(self, key, *, version_id=None):
        try:
            result = self.bucket.head_object(self._key(key), params=self._params(version_id))
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                raise ObjectMissing("Object does not exist") from exc
            raise StorageError("OSS metadata request failed") from exc
        return ObjectInfo(int(result.content_length), getattr(result, "etag", None), getattr(result, "versionid", None), str(getattr(result, "server_crc", "")) or None)

    @contextmanager
    def reader(self, key, *, version_id=None):
        result = self.bucket.get_object(self._key(key), params=self._params(version_id))
        try:
            yield result
        finally:
            result.close()

    def put_file(self, path, key, mime_type, *, checkpoint=None, on_checkpoint=None):
        from oss2.models import PartInfo
        sha, size = file_digest(path)
        object_key = self._key(key)
        if size < self.part_size:
            with Path(path).open("rb") as stream:
                self.bucket.put_object(object_key, stream, headers={"Content-Type": mime_type, "x-oss-meta-sha256": sha})
            return self.head(key)
        part_size = max(self.part_size, math.ceil(size / 10000))
        state = dict(checkpoint or {})
        if state and (state.get("key"), state.get("sha256"), state.get("part_size")) != (key, sha, part_size):
            raise StorageError("Multipart checkpoint does not match source")
        if not state:
            result = self.bucket.init_multipart_upload(object_key, headers={"Content-Type": mime_type, "x-oss-meta-sha256": sha})
            state = {"key": key, "sha256": sha, "part_size": part_size, "upload_id": result.upload_id}
        try:
            if on_checkpoint:
                on_checkpoint(dict(state))
            existing, marker = {}, ""
            while True:
                page = self.bucket.list_parts(object_key, state["upload_id"], marker=marker)
                existing.update({p.part_number: p for p in page.parts})
                if not page.is_truncated:
                    break
                marker = page.next_marker
            parts = []
            with Path(path).open("rb") as stream:
                number = 1
                while block := stream.read(part_size):
                    prior = existing.get(number)
                    if prior and prior.size == len(block):
                        etag = prior.etag
                    else:
                        etag = self.bucket.upload_part(object_key, state["upload_id"], number, block).etag
                    parts.append(PartInfo(number, etag))
                    if on_checkpoint:
                        state["completed_parts"] = number
                        on_checkpoint(dict(state))
                    number += 1
            self.bucket.complete_multipart_upload(object_key, state["upload_id"], parts)
        except Exception:
            if not on_checkpoint:
                self.bucket.abort_multipart_upload(object_key, state["upload_id"])
            raise
        return self.head(key)

    def presign(self, key, expires_in, *, version_id=None):
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        return self.signing_bucket.sign_url("GET", self._key(key), expires_in, params=self._params(version_id), slash_safe=True)

    def delete(self, key, *, version_id=None):
        self.bucket.delete_object(self._key(key), params=self._params(version_id))

    def read_range(self, key, start, end, *, version_id=None):
        result = self.bucket.get_object(self._key(key), byte_range=(start, end), params={"versionId": version_id} if version_id else None)
        try:
            if result.status != 206 or not result.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
                raise StorageError("OSS did not honor byte range")
            return result.read(end - start + 2)
        finally:
            result.close()
