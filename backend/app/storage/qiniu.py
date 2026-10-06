"""Kodo's S3 data API and native signed CDN download URLs."""
import base64
import hashlib
import hmac
import time

from .base import StorageError
from .cloud import S3Storage, public_object_url


class QiniuStorage(S3Storage):
    kind = "qiniu"

    def __init__(self, config, credentials, **kwargs):
        super().__init__(config, credentials, **kwargs)
        self._credentials = credentials

    def presign(self, key, expires_in, *, version_id=None):
        if not 1 <= expires_in <= 3600:
            raise StorageError("Playback URL lifetime must be 1 to 3600 seconds")
        if not self.config.get("public_base_url"):
            return super().presign(key, expires_in, version_id=version_id)
        if version_id:
            # CDN URLs address the latest object, never silently discard a version.
            return self.signing_client.generate_presigned_url("get_object", Params=self._args(key, version_id), ExpiresIn=expires_in)
        url = public_object_url(self.config["public_base_url"], self._key(key))
        if not self.config.get("private_bucket", True):
            return url
        url += "?e=" + str(int(time.time()) + expires_in)
        signature = base64.urlsafe_b64encode(hmac.new(self._credentials["secret_access_key"].encode(), url.encode(), hashlib.sha1).digest()).decode()
        return url + "&token=" + self._credentials["access_key_id"] + ":" + signature
