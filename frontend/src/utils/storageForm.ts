type Values = Record<string, any>;
export function storageKindName(kind: string) {
  return (
    ({ local: '本地目录', s3: 'S3 兼容存储', oss: '阿里云 OSS' } as Record<string, string>)[kind] ||
    '其他存储'
  );
}
export type StorageDraft = {
  name: string;
  kind: 'local' | 's3' | 'oss';
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
function endpoint(value: unknown, label: string) {
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
      !['', '/'].includes(url.pathname)
    )
      throw new Error();
  } catch {
    throw new Error(`${label}须为完整 HTTP(S) 地址，不能包含账号、路径或查询参数`);
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
      : [
          'bucket',
          'endpoint',
          'public_endpoint',
          'region',
          'prefix',
          'read_priority',
          'part_size',
          ...(draft.kind === 's3' ? ['addressing_style'] : []),
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
  if (!config.bucket) throw new Error('请填写存储桶名称');
  if (draft.kind === 'oss' && (!config.endpoint || !config.region))
    throw new Error('原生 OSS 需要填写 Endpoint 和 Region');
  endpoint(config.endpoint, 'Endpoint');
  endpoint(config.public_endpoint, '播放 Endpoint');
  if (
    config.prefix &&
    (String(config.prefix).includes('\\') ||
      String(config.prefix)
        .split('/')
        .some((p) => p === '.' || p === '..'))
  )
    throw new Error('对象前缀不能包含反斜杠或上级目录');
  const secretKey = draft.kind === 's3' ? 'secret_access_key' : 'access_key_secret';
  const tokenKey = draft.kind === 's3' ? 'session_token' : 'security_token';
  const credentials = Object.fromEntries(
    ['access_key_id', secretKey, tokenKey]
      .filter((key) => typeof draft.credentials[key] === 'string' && draft.credentials[key].trim())
      .map((key) => [key, draft.credentials[key].trim()]),
  );
  if (
    Object.keys(credentials).length ||
    !draft.editing ||
    draft.credentialsChangedKind ||
    !draft.hasCredentials
  ) {
    if (!credentials.access_key_id || !credentials[secretKey])
      throw new Error('请完整填写 Access Key ID 与密钥；留空仅能保留同类型的现有凭据');
    result.credentials = credentials;
  }
  return result;
}
