"""Bounded, read-only bucket/drive discovery. Provider errors never escape to UI."""
from .base import StorageError
from .configuration import GRAPH_KINDS, S3_KINDS, normalized_config, validate_configuration, validate_credentials

LIMIT = 500
PAGES = 5


def discover_storage(kind, config, credentials, *, on_credentials=None):
    validate_configuration(kind, config, discovery=True)
    validate_credentials(kind, credentials)
    config = normalized_config(kind, config)
    try:
        if kind == "local":
            return {"kind": kind, "items": [], "message": "本地目录请使用已挂载的媒体目录", "truncated": False}
        if kind == "upyun":
            return {"kind": kind, "items": [], "message": "又拍云操作员接口不能枚举服务；请填写控制台的服务名称", "truncated": False}
        if kind in GRAPH_KINDS:
            from .graph import GraphStorage
            adapter = GraphStorage(kind, config, credentials, purpose="discovery", on_credentials=on_credentials)
            from urllib.parse import quote
            if kind.startswith("sharepoint"):
                site = config.get("site_id")
                if not site:
                    return {"kind": kind, "items": [], "message": "请填写 SharePoint 站点 ID，或 tenant.sharepoint.com:/sites/站点名称", "truncated": False}
                # Resolve both compound IDs and hostname:/sites/path identifiers.
                site_id = adapter._api("GET", "/sites/" + quote(site, safe=":,/" )).json()["id"]
                path = "/sites/" + quote(site_id, safe="") + "/drives?$top=100"
            else:
                path = "/me/drives?$top=100"
            items, truncated = [], False
            for _ in range(PAGES):
                page = adapter._api("GET", path).json()
                items.extend({"id": item["id"], "name": item.get("name") or "默认云盘"} for item in page.get("value", []))
                path = page.get("@odata.nextLink")
                if not path or len(items) >= LIMIT:
                    truncated = bool(path)
                    break
            else:
                truncated = True
            return {"kind": kind, "items": items[:LIMIT], "truncated": truncated}
        if kind == "oss":
            import oss2
            from oss2.credentials import StaticCredentialsProvider
            from .cloud import safe_oss_session
            auth = oss2.ProviderAuthV4(StaticCredentialsProvider(credentials["access_key_id"], credentials["access_key_secret"], credentials.get("security_token")))
            endpoint = config.get("endpoint") or "https://oss-cn-hangzhou.aliyuncs.com"
            region = config.get("region")
            if region == "us-east-1":
                region = "cn-hangzhou"
            session = safe_oss_session()
            service = oss2.Service(auth, endpoint, region=region, connect_timeout=(3, 5), session=session)
            try:
                page = service.list_buckets(max_keys=LIMIT)
            finally:
                session.session.close()
            items = []
            for bucket in page.buckets:
                location = getattr(bucket, "location", "")
                region = location.removeprefix("oss-")
                value = {"name": bucket.name}
                if region:
                    value.update(region=region, endpoint="https://oss-" + region + ".aliyuncs.com")
                items.append(value)
            return {"kind": kind, "items": items[:LIMIT], "truncated": bool(page.is_truncated)}
        if kind == "obs":
            from .obs import checked, obs_client
            client = obs_client(config, credentials, purpose="discovery")
            try:
                page = checked(client.listBuckets(isQueryLocation=True, maxKeys=LIMIT))
                items = [{"name": bucket.name, **({"region": bucket.location, "endpoint": "https://obs." + bucket.location + ".myhuaweicloud.com"} if bucket.location else {})} for bucket in (page.buckets or [])]
                return {"kind": kind, "items": items[:LIMIT], "truncated": bool(page.isTruncated or page.nextMarker) or len(items) >= LIMIT}
            finally:
                client.close()
        if kind in S3_KINDS:
            from .cloud import S3Storage
            from botocore.exceptions import ClientError
            adapter = S3Storage({**config, "bucket": config.get("bucket") or "discovery-placeholder"}, credentials, purpose="discovery")
            try:
                try:
                    page = adapter.client.list_buckets(MaxBuckets=LIMIT)
                except ClientError as error:
                    if error.response.get("Error", {}).get("Code") not in {"InvalidArgument", "InvalidRequest", "NotImplemented"}:
                        raise
                    # Some compatible services predate ListBuckets pagination.
                    page = adapter.client.list_buckets()
                items = []
                for bucket in page.get("Buckets", [])[:LIMIT]:
                    value = {"name": bucket["Name"]}
                    region = bucket.get("BucketRegion")
                    if region:
                        value["region"] = region
                        endpoint = normalized_config(kind, {"region": region}).get("endpoint")
                        if endpoint:
                            value["endpoint"] = endpoint
                    items.append(value)
                return {"kind": kind, "items": items, "truncated": bool(page.get("ContinuationToken")) or len(page.get("Buckets", [])) > LIMIT}
            finally:
                adapter.client.close()
                if adapter.signing_client is not adapter.client:
                    adapter.signing_client.close()
    except StorageError:
        raise
    except Exception:
        # Error messages/response bodies may contain signatures and bearer tokens.
        raise StorageError("无法获取存储列表，请检查服务地址、凭据和列举权限；也可以手动填写名称") from None
    raise StorageError("此存储服务不支持自动发现")
