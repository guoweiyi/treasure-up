type Values = Record<string, any>;
export const storageProviders = [
  { kind: 'local', name: '本地目录 / NAS', group: '本地' },
  { kind: 'oss', name: '阿里云 OSS', group: '对象存储' },
  { kind: 'cos', name: '腾讯云 COS', group: '对象存储' },
  { kind: 'obs', name: '华为云 OBS', group: '对象存储' },
  { kind: 'upyun', name: '又拍云', group: '对象存储' },
  { kind: 'qiniu', name: '七牛云', group: '对象存储' },
  { kind: 'minio', name: 'MinIO', group: '对象存储' },
  { kind: 'rain', name: '雨云 Rain S3', group: '对象存储' },
  { kind: 'spaces', name: 'DigitalOcean Spaces', group: '对象存储' },
  { kind: 'r2', name: 'Cloudflare R2', group: '对象存储' },
  { kind: 'oracle', name: 'Oracle 对象存储', group: '对象存储' },
  { kind: 'b2', name: 'Backblaze B2', group: '对象存储' },
  { kind: 's3', name: 'Amazon S3 / S3 兼容协议', group: '对象存储' },
  { kind: 'onedrive', name: 'OneDrive', group: '云盘' },
  { kind: 'onedrive_cn', name: 'OneDrive · 世纪互联', group: '云盘' },
  { kind: 'sharepoint', name: 'SharePoint', group: '云盘' },
  { kind: 'sharepoint_cn', name: 'SharePoint · 世纪互联', group: '云盘' },
] as const;
export type StorageKind = (typeof storageProviders)[number]['kind'];
export const graphStorage = (kind: string) =>
  ['onedrive', 'onedrive_cn', 'sharepoint', 'sharepoint_cn'].includes(kind);
export const s3Storage = (kind: string) =>
  ['s3', 'cos', 'minio', 'rain', 'spaces', 'r2', 'oracle', 'b2', 'qiniu'].includes(kind);
export function storageCredentialFields(kind: StorageKind) {
  if (graphStorage(kind))
    return [
      { key: 'client_id', label: '应用 ID · Client ID', required: true },
      { key: 'client_secret', label: '应用密钥 · Client Secret（可选）', required: false },
      { key: 'refresh_token', label: '刷新令牌 · Refresh Token', required: true },
    ];
  if (kind === 'upyun')
    return [
      { key: 'operator', label: '操作员', required: true },
      { key: 'password', label: '操作员密码', required: true },
      { key: 'token_secret', label: 'URL 防盗链密钥（私有播放时填写）', required: false },
    ];
  if (kind === 'local') return [];
  const native = kind === 'oss';
  return [
    { key: 'access_key_id', label: kind === 'cos' ? 'Secret ID' : 'Access Key ID', required: true },
    {
      key: native ? 'access_key_secret' : 'secret_access_key',
      label: native ? 'Access Key Secret' : 'Secret Access Key',
      required: true,
    },
    ...(['qiniu'].includes(kind)
      ? []
      : [
          {
            key: native ? 'security_token' : 'session_token',
            label: '临时安全令牌（可选）',
            required: false,
          },
        ]),
  ];
}
export function storageKindName(kind: string) {
  return storageProviders.find((provider) => provider.kind === kind)?.name || '其他存储';
}
export type StorageDraft = {
  name: string;
  kind: StorageKind;
  id?: string;
  config: Values;
  credentials: Values;
  is_default: boolean;
  enabled: boolean;
  originalKind: string;
  originalConfig: Values;
  credentialsChangedKind: boolean;
  hasCredentials: boolean;
  editing: boolean;
};
export function storageDraft(row?: Values): StorageDraft {
  return {
    name: row?.name || '',
    id: row?.id,
    kind: row?.kind || 'local',
    config: { ...row?.config },
    credentials: {},
    is_default: !!row?.is_default,
    enabled: row?.enabled ?? true,
    originalKind: row?.kind || 'local',
    originalConfig: { ...row?.config },
    credentialsChangedKind: false,
    hasCredentials: !!row?.has_credentials,
    editing: !!row?.id,
  };
}
export function changeStorageKind(draft: StorageDraft, kind: StorageDraft['kind']) {
  if (kind === draft.kind) return;
  draft.kind = kind;
  draft.config = {};
  draft.credentials = {};
  draft.credentialsChangedKind = true;
}
function endpoint(value: unknown, label: string, allowPath = false) {
  if (!value) return;
  try {
    const url = new URL(String(value));
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      !url.hostname ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      (!allowPath && !['', '/'].includes(url.pathname))
    )
      throw new Error();
  } catch {
    throw new Error(
      `${label}须为完整 HTTP(S) 地址，不能包含账号、${allowPath ? '' : '路径或'}查询参数`,
    );
  }
}
export function storagePayload(draft: StorageDraft) {
  if (!draft.name.trim()) throw new Error('请填写存储名称');
  if (draft.is_default && !draft.enabled) throw new Error('默认写入位置必须启用');
  const config: Values =
    draft.kind === draft.originalKind && !draft.credentialsChangedKind
      ? { ...draft.originalConfig }
      : {};
  const keys =
    draft.kind === 'local'
      ? ['root', 'read_priority']
      : graphStorage(draft.kind)
        ? ['drive_id', 'site_id', 'tenant_id', 'prefix', 'read_priority', 'delivery_mode']
        : [
            'bucket',
            'endpoint',
            'public_endpoint',
            'public_base_url',
            'private_bucket',
            'delivery_mode',
            'region',
            'prefix',
            'read_priority',
            'part_size',
            ...(s3Storage(draft.kind) ? ['addressing_style'] : []),
          ];
  for (const key of keys) {
    const value = draft.config[key];
    if (value !== '' && value != null)
      config[key] = typeof value === 'string' ? value.trim() : value;
    else if (
      !(
        Object.hasOwn(draft.originalConfig, key) &&
        value === draft.originalConfig[key] &&
        !draft.credentialsChangedKind
      )
    )
      delete config[key];
  }
  if (
    config.read_priority != null &&
    (!Number.isInteger(config.read_priority) ||
      config.read_priority < 0 ||
      config.read_priority > 10000)
  )
    throw new Error('读取优先级须为 0 至 10000 的整数');
  if (
    config.part_size != null &&
    (!Number.isInteger(config.part_size) ||
      config.part_size < 5 * 1024 ** 2 ||
      config.part_size > 512 * 1024 ** 2)
  )
    throw new Error('分片大小须为 5 至 512 MiB');
  const result: Values = {
    name: draft.name.trim(),
    kind: draft.kind,
    config,
    is_default: draft.is_default,
    enabled: draft.enabled,
  };
  if (draft.kind === 'local') {
    if (draft.credentialsChangedKind) result.credentials = {};
    return result;
  }
  if (graphStorage(draft.kind)) {
    if (!config.drive_id) throw new Error('请选择云盘 / 文档库，或填写 Drive ID');
    if (draft.kind.startsWith('sharepoint') && !config.site_id)
      throw new Error('请填写 SharePoint 站点');
  } else if (!config.bucket)
    throw new Error(draft.kind === 'upyun' ? '请填写服务名称' : '请填写存储桶名称');
  if (draft.kind === 'oss' && !config.endpoint && !config.region)
    throw new Error('阿里云 OSS 需要填写 API 服务地址或区域');
  endpoint(config.endpoint, 'Endpoint');
  endpoint(config.public_endpoint, '播放 Endpoint');
  endpoint(config.public_base_url, '自定义播放域名');
  if (draft.kind === 'upyun' && !config.public_base_url)
    throw new Error('请填写此服务的自定义播放域名');
  if (config.delivery_mode && !['auto', 'direct', 'redirect'].includes(config.delivery_mode))
    throw new Error('请选择有效的播放方式');
  if (config.private_bucket === false && !config.public_base_url)
    throw new Error('公开 / CDN 播放需要填写自定义播放域名');
  if (
    s3Storage(draft.kind) &&
    draft.kind !== 'qiniu' &&
    config.public_base_url &&
    config.private_bucket !== false
  )
    throw new Error('此服务的私有播放请填写签名外网地址；使用 CDN 时请选择公开域名 / CDN');
  if (
    config.prefix &&
    (String(config.prefix).includes('\\') ||
      String(config.prefix)
        .split('/')
        .some((p) => p === '.' || p === '..'))
  )
    throw new Error('对象前缀不能包含反斜杠或上级目录');
  const fields = storageCredentialFields(draft.kind);
  const credentials = Object.fromEntries(
    fields
      .map((field) => field.key)
      .filter((key) => typeof draft.credentials[key] === 'string' && draft.credentials[key].trim())
      .map((key) => [key, draft.credentials[key].trim()]),
  );
  if (
    Object.keys(credentials).length ||
    !draft.editing ||
    draft.credentialsChangedKind ||
    !draft.hasCredentials
  ) {
    if (fields.some((field) => field.required && !credentials[field.key]))
      throw new Error('请完整填写此类型的访问凭据；全部留空可保留现有凭据');
    result.credentials = credentials;
    if (draft.kind === 'upyun' && config.private_bucket !== false && !credentials.token_secret)
      throw new Error('又拍云私有播放需要填写 URL 防盗链密钥，或选择公开域名播放');
  }
  return result;
}
export function storageDiscoveryPayload(draft: StorageDraft) {
  const credentials = Object.fromEntries(
    Object.entries(draft.credentials)
      .filter(
        ([key, value]) =>
          storageCredentialFields(draft.kind).some((field) => field.key === key) &&
          typeof value === 'string' &&
          value.trim(),
      )
      .map(([key, value]) => [key, value.trim()]),
  );
  const retain =
    draft.editing &&
    draft.hasCredentials &&
    !draft.credentialsChangedKind &&
    !Object.keys(credentials).length;
  if (
    !retain &&
    storageCredentialFields(draft.kind).some((field) => field.required && !credentials[field.key])
  )
    throw new Error('请先完整填写访问凭据');
  return {
    kind: draft.kind,
    config: { ...draft.config },
    ...(retain ? { profile_id: draft.id } : { credentials }),
  };
}
