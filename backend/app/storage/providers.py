from .cloud import OssStorage, S3Storage
from .configuration import GRAPH_KINDS, S3_KINDS, normalized_config
from .base import StorageError


def create_cloud_adapter(kind, config, credentials, *, purpose="default", on_credentials=None):
    config = normalized_config(kind, config)
    if kind in S3_KINDS:
        if kind == "qiniu":
            from .qiniu import QiniuStorage
            return QiniuStorage(config, credentials, purpose=purpose)
        adapter = S3Storage(config, credentials, purpose=purpose)
        adapter.kind = kind
        return adapter
    if kind == "oss":
        return OssStorage(config, credentials, purpose=purpose)
    if kind == "obs":
        from .obs import ObsStorage
        return ObsStorage(config, credentials, purpose=purpose)
    if kind == "upyun":
        from .upyun import UpyunStorage
        return UpyunStorage(config, credentials, purpose=purpose)
    if kind in GRAPH_KINDS:
        from .graph import GraphStorage
        return GraphStorage(kind, config, credentials, purpose=purpose, on_credentials=on_credentials)
    raise StorageError("不支持的存储服务")
