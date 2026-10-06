"""Provider configuration shared by forms, discovery and adapter construction."""
from __future__ import annotations

import re
from pathlib import Path

from .base import StorageError, safe_key
from .cloud import _endpoint

S3_KINDS = frozenset({"s3", "cos", "minio", "rain", "spaces", "r2", "oracle", "b2", "qiniu"})
GRAPH_KINDS = frozenset({"onedrive", "onedrive_cn", "sharepoint", "sharepoint_cn"})
KINDS = S3_KINDS | GRAPH_KINDS | {"local", "oss", "obs", "upyun"}


def normalized_config(kind: str, config: dict) -> dict:
    result = dict(config)
    region = result.get("region")
    if kind == "oss" and not region and result.get("endpoint"):
        from urllib.parse import urlparse
        host = urlparse(result["endpoint"]).hostname or ""
        match = re.fullmatch(r"oss-([a-z0-9-]+?)(?:-internal)?\.aliyuncs\.com", host)
        if match:
            result["region"] = region = match.group(1)
    if kind == "qiniu" and not region and result.get("endpoint"):
        from urllib.parse import urlparse
        host = urlparse(result["endpoint"]).hostname or ""
        match = re.fullmatch(r"s3[.-]([a-z0-9-]+)\.qiniucs\.com", host)
        if match:
            result["region"] = region = match.group(1)
    if not result.get("endpoint"):
        templates = {"cos": "https://cos.{region}.myqcloud.com", "obs": "https://obs.{region}.myhuaweicloud.com",
                     "oss": "https://oss-{region}.aliyuncs.com", "spaces": "https://{region}.digitaloceanspaces.com",
                     "qiniu": "https://s3.{region}.qiniucs.com", "b2": "https://s3.{region}.backblazeb2.com"}
        if kind in templates and region:
            result["endpoint"] = templates[kind].format(region=region)
    if kind == "r2":
        result.setdefault("region", "auto")
    else:
        result.setdefault("region", "us-east-1")
    if kind in {"minio", "rain", "r2", "oracle"}:
        result.setdefault("addressing_style", "path")
    return result


def validate_configuration(kind: str, config: dict, *, discovery=False) -> None:
    if kind not in KINDS or not isinstance(config, dict):
        raise StorageError("不支持的存储服务")
    text_fields = {"root", "prefix", "endpoint", "public_endpoint", "public_base_url", "region", "bucket",
                   "addressing_style", "delivery_mode", "tenant_id", "drive_id", "site_id"}
    allowed = text_fields | {"read_priority", "part_size", "private_bucket"}
    if set(config) - allowed:
        raise StorageError("存储配置含未知字段；密钥请填写在独立凭据栏")
    for key in text_fields:
        if key in {"endpoint", "public_endpoint", "public_base_url"} and config.get(key) is None:
            continue
        if key in config and (not isinstance(config[key], str) or len(config[key]) > 2048):
            raise StorageError("存储地址、目录和名称须为文本")
    for key in ("endpoint", "public_endpoint", "public_base_url"):
        if config.get(key):
            try:
                _endpoint(config[key])
            except StorageError:
                raise StorageError("服务地址须为完整 HTTP(S) 地址，不要包含账号、路径或参数") from None
    if config.get("prefix"):
        safe_key(config["prefix"].strip("/"))
    if "read_priority" in config and (type(config["read_priority"]) is not int or not 0 <= config["read_priority"] <= 10000):
        raise StorageError("读取优先级须为 0 至 10000 的整数")
    if "part_size" in config and (type(config["part_size"]) is not int or not 5 * 1024**2 <= config["part_size"] <= 512 * 1024**2):
        raise StorageError("分片大小须为 5 至 512 MiB")
    if "private_bucket" in config and type(config["private_bucket"]) is not bool:
        raise StorageError("私有空间选项须为开关")
    if config.get("addressing_style", "virtual") not in {"virtual", "path", "auto"}:
        raise StorageError("请选择有效的存储桶寻址方式")
    if config.get("delivery_mode", "auto") not in {"auto", "direct", "redirect"}:
        raise StorageError("请选择有效的播放访问方式")
    if kind == "local":
        from app.config import settings
        root = Path(config.get("root") or settings.media_root).resolve()
        if not root.is_relative_to(settings.media_root.resolve()):
            raise StorageError("本地存储目录必须位于已挂载媒体根目录内")
        return
    if kind in GRAPH_KINDS:
        if not discovery and not config.get("drive_id"):
            raise StorageError("请先获取并选择云盘或文档库")
        if config.get("tenant_id") and not re.fullmatch(r"[A-Za-z0-9.-]+", config["tenant_id"]):
            raise StorageError("租户 ID 格式不正确")
        if config.get("site_id") and ("?" in config["site_id"] or "#" in config["site_id"] or ".." in config["site_id"]):
            raise StorageError("站点 ID 格式不正确")
        return
    if not discovery and not config.get("bucket"):
        raise StorageError("请填写存储桶或服务名称")
    if config.get("bucket") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", config["bucket"]):
        raise StorageError("存储桶名称格式不正确")
    if config.get("region") and not re.fullmatch(r"[A-Za-z0-9-]{1,80}", config["region"]):
        raise StorageError("区域格式不正确")
    if kind in {"minio", "rain", "r2", "oracle", "qiniu", "b2", "spaces", "cos", "obs"} and not normalized_config(kind, config).get("endpoint"):
        raise StorageError("请填写此服务的 API 地址或区域")
    # A SigV4 signature includes Host. A CDN cannot be substituted after signing.
    if kind in S3_KINDS - {"qiniu"} and config.get("public_base_url") and config.get("private_bucket", True):
        raise StorageError("S3 私有桶请使用外网签名 API 地址；自定义 CDN 域名需选择公开读取或 CDN 回源鉴权")
    if kind == "upyun" and not discovery and not config.get("public_base_url"):
        raise StorageError("又拍云请填写已绑定的访问域名")


def validate_credentials(kind: str, credentials: dict) -> None:
    if kind == "local":
        return
    if not isinstance(credentials, dict):
        raise StorageError("请填写此存储服务的访问凭据")
    if kind in GRAPH_KINDS:
        required, allowed = {"client_id", "refresh_token"}, {"client_id", "client_secret", "refresh_token"}
    elif kind == "upyun":
        required, allowed = {"operator", "password"}, {"operator", "password", "token_secret"}
    elif kind == "oss":
        required, allowed = {"access_key_id", "access_key_secret"}, {"access_key_id", "access_key_secret", "security_token"}
    else:
        required, allowed = {"access_key_id", "secret_access_key"}, {"access_key_id", "secret_access_key", "session_token"}
    if set(credentials) - allowed:
        raise StorageError("凭据含不适用于此服务的字段")
    if any(not isinstance(credentials.get(key), str) or not credentials[key].strip() for key in required):
        raise StorageError("请完整填写此存储服务的访问凭据")
    if any(not isinstance(value, str) or len(value) > 16384 or "\x00" in value for value in credentials.values()):
        raise StorageError("访问凭据格式不正确")
