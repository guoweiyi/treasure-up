<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElConfigProvider,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  vLoading,
} from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';
import {
  api,
  write,
  query,
  errorText,
  statusText,
  date,
  bytes,
  session,
  loadDisplaySettings,
} from '../api';
import type { Row, Page } from '../types';
const route = useRoute();
const sections = [
  { id: 'overview', name: '总览', group: '运行' },
  { id: 'accounts', name: 'B 站账号', group: '采集' },
  { id: 'sources', name: '收藏来源', group: '采集' },
  { id: 'jobs', name: '任务中心', group: '采集' },
  { id: 'videos', name: '视频标注', group: '内容' },
  { id: 'creators', name: 'UP 主整理', group: '内容' },
  { id: 'storage', name: '存储位置', group: '维护' },
  { id: 'backups', name: '备份记录', group: '维护' },
  { id: 'settings', name: '系统配置', group: '维护' },
  { id: 'users', name: '访问权限', group: '维护' },
  { id: 'audit', name: '操作记录', group: '维护' },
];
const section = computed(() => String(route.params.section || 'overview'));
const isAdministrator = computed(() => session.user?.role === 'admin');
const allowed = computed(
  () => isAdministrator.value || ['videos', 'creators'].includes(section.value),
);
const menu = computed(() =>
  sections.filter((s) => isAdministrator.value || ['videos', 'creators'].includes(s.id)),
);
const heading = computed(() => sections.find((s) => s.id === section.value)?.name || '管理后台');
const descriptions: Record<string, string> = {
  overview: '查看归档运行状态与最近任务。',
  accounts: '授权账号仅用于后台采集，凭据不会返回浏览器。',
  sources: '管理需要归档的收藏夹及扫描周期。',
  jobs: '查看实际进度、错误原因和可恢复的检查点。',
  videos: '本地标注与来源信息分开保存，后续同步保留你的整理。',
  creators: '整理 UP 主的别名、简介、笔记和标签。',
  storage: '配置本地、S3 或原生 OSS。切换默认位置不自动移动已有文件。',
  backups: '记录真实备份任务。已完成的备份不等于已经通过恢复演练。',
  settings: '展示设置、采集默认策略与独立备份目录。',
  users: '本站账号与 B 站采集账号相互独立。',
  audit: '查看本站记录的管理操作。',
};
const rows = ref<Row[]>([]),
  total = ref(0),
  page = ref(1),
  q = ref(''),
  status = ref(''),
  busy = ref(false),
  error = ref(''),
  actionBusy = ref(''),
  overview = ref<Row | null>(null),
  feedback = ref<Row | null>(null);
const statuses = [
  'queued',
  'running',
  'paused',
  'blocked',
  'partial',
  'succeeded',
  'failed',
  'cancelled',
];
const jobNames: Record<string, string> = {
  scan_collection: '扫描收藏夹',
  archive_video: '归档视频',
  refresh_comments: '补采评论',
  verify_account: '验证账号',
  migrate_storage: '迁移存储',
  backup: '创建备份',
  probe_storage: '探测存储能力',
};
const dialog = ref(false),
  dialogTitle = ref(''),
  dialogKind = ref(''),
  editId = ref(''),
  submitting = ref(false),
  formError = ref(''),
  form = reactive<Row>({}),
  accounts = ref<Row[]>([]),
  storages = ref<Row[]>([]),
  targets = ref<Row[]>([]);
const settingsForm = reactive<Row>({
  display: { site_name: 'Treasure Up', default_danmaku: true },
  ingest: {
    quality: 'best',
    create_compatible_copy: true,
    prefer_h264: false,
    request_budget: 100,
    download_media: true,
    fetch_comments: true,
    fetch_danmaku: true,
    fetch_subtitles: true,
    include_auto_subtitles: true,
    max_download_bytes: 30000000000,
    max_pages: 100,
  },
  backup: { destination: '', key_id: '', interval_hours: 12, retention_days: 30, enabled: false },
});
const qualityOptions = [
  { value: 'best', label: '最高可见画质' },
  { value: '4320p', label: '最高 8K（4320P）' },
  { value: '2160p', label: '最高 4K（2160P）' },
  { value: '1440p', label: '最高 1440P' },
  { value: '1080p', label: '最高 1080P' },
  { value: '720p', label: '最高 720P' },
  { value: '480p', label: '最高 480P' },
  { value: '360p', label: '最高 360P' },
];
const settingsOriginal = ref<Row>({});
let sequence = 0,
  timer: ReturnType<typeof setInterval> | undefined;
const endpoint = computed(() =>
  ['videos', 'creators'].includes(section.value) ? `/${section.value}` : `/admin/${section.value}`,
);
async function load(p = 1, quiet = false) {
  if (!allowed.value) return;
  const n = ++sequence;
  page.value = p;
  if (!quiet) busy.value = true;
  error.value = '';
  try {
    if (section.value === 'overview') {
      const d = await api<Row>('/admin/overview');
      if (n === sequence) overview.value = d;
    } else if (section.value === 'settings') {
      const d = await api<Row>('/admin/settings');
      if (n !== sequence) return;
      settingsOriginal.value = d;
      for (const k of ['display', 'ingest', 'backup']) Object.assign(settingsForm[k], d[k] || {});
      if (settingsForm.ingest.quality === '8k') settingsForm.ingest.quality = '4320p';
      if (settingsForm.ingest.quality === '4k') settingsForm.ingest.quality = '2160p';
    } else {
      const d = await api<Page<Row>>(
        `${endpoint.value}?${query({ page: p, page_size: 20, q: q.value, status: status.value })}`,
      );
      if (n === sequence) {
        rows.value = d.items;
        total.value = d.total;
      }
    }
  } catch (e) {
    if (n === sequence) error.value = errorText(e);
  } finally {
    if (n === sequence) busy.value = false;
  }
}
async function loadChoices() {
  const results = await Promise.allSettled([
    api<Page<Row>>('/admin/accounts?page_size=100'),
    api<Page<Row>>('/admin/storage?page_size=100'),
  ]);
  if (results[0].status === 'fulfilled') accounts.value = results[0].value.items;
  if (results[1].status === 'fulfilled') storages.value = results[1].value.items;
}
function parseObject(value: string, label: string) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value || '{}');
  } catch {
    throw new Error(`${label}不是有效的 JSON`);
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    throw new Error(`${label}必须为 JSON 对象`);
  return parsed as Row;
}
function policyOverride(key: string) {
  try {
    const value = parseObject(form.policy_text || '{}', '策略')[key];
    return value === undefined ? 'inherit' : String(value);
  } catch {
    return 'inherit';
  }
}
function setPolicyOverride(key: string, value: string) {
  try {
    const policy = parseObject(form.policy_text || '{}', '策略');
    if (value === 'inherit') delete policy[key];
    else policy[key] = value === 'true';
    form.policy_text = JSON.stringify(policy, null, 2);
    formError.value = '';
  } catch (e) {
    formError.value = errorText(e);
  }
}
function tags(value: string) {
  return [
    ...new Set(
      value
        .split(/[,，\n]/)
        .map((t) => t.trim())
        .filter(Boolean),
    ),
  ];
}
function resetForm() {
  Object.keys(form).forEach((k) => delete form[k]);
  formError.value = '';
}
async function openEditor(kind = section.value, row?: Row) {
  resetForm();
  editId.value = row?.id || '';
  dialogKind.value = kind;
  dialogTitle.value = `${row ? '编辑' : '添加'}${({ accounts: 'B 站账号', sources: '收藏来源', jobs: '任务', storage: '存储位置', users: '本站用户', videos: '视频标注', creators: 'UP 主资料' } as Row)[kind] || ''}`;
  if (['accounts', 'sources', 'jobs', 'storage', 'users'].includes(kind)) await loadChoices();
  if (kind === 'accounts') Object.assign(form, { name: '', cookie: '' });
  if (kind === 'sources')
    Object.assign(form, {
      source_id: '',
      title: '',
      account_id: accounts.value[0]?.id || '',
      enabled: true,
      interval_minutes: 360,
      policy_text: '{}',
      ...row,
      policy_text_override: undefined,
    });
  if (kind === 'sources' && row) form.policy_text = JSON.stringify(row.policy || {}, null, 2);
  if (kind === 'jobs') {
    Object.assign(form, {
      kind: 'archive_video',
      target_id: '',
      account_id: accounts.value[0]?.id || '',
      policy_text: '{}',
    });
    await loadTargets();
  }
  if (kind === 'storage')
    Object.assign(form, {
      name: '',
      kind: 'local',
      credentials_text: '',
      is_default: false,
      enabled: true,
      ...row,
      config_text: JSON.stringify(row?.config || {}, null, 2),
    });
  if (kind === 'users')
    Object.assign(form, { username: '', password: '', role: 'reader', disabled: false, ...row });
  if (kind === 'videos') {
    try {
      const d = await api<Row>(`/videos/${row!.id}`);
      Object.assign(form, {
        source_title: d.source_title || d.title,
        source_description: d.source_description || '',
        title_override: d.title_override ?? (d.source_title !== d.title ? d.title : ''),
        description_override: d.description_override ?? '',
        notes: d.notes || '',
        tags_text: (d.tags || []).join('，'),
        starred: d.starred,
      });
    } catch (e) {
      ElMessage.error(errorText(e));
      return;
    }
  }
  if (kind === 'creators') {
    try {
      const d = await api<Row>(`/creators/${row!.id}`);
      Object.assign(form, {
        source_name: d.source_name,
        alias: d.alias ?? (d.source_name !== d.name ? d.name : ''),
        description_override: d.description_override ?? '',
        notes: d.notes || '',
        tags_text: (d.tags || []).join('，'),
      });
    } catch (e) {
      ElMessage.error(errorText(e));
      return;
    }
  }
  dialog.value = true;
}
async function loadTargets() {
  targets.value = [];
  form.target_id = '';
  try {
    const path =
      form.kind === 'scan_collection'
        ? '/collections'
        : form.kind === 'verify_account'
          ? '/admin/accounts'
          : '/videos';
    targets.value = (await api<Page<Row>>(`${path}?page_size=100`)).items;
  } catch (e) {
    formError.value = errorText(e);
  }
}
async function submit() {
  submitting.value = true;
  formError.value = '';
  try {
    const kind = dialogKind.value;
    let payload: Row;
    if (kind === 'accounts') {
      if (!form.name?.trim() || !form.cookie?.trim()) throw new Error('请填写账号名称和 Cookie');
      payload = { name: form.name.trim(), cookie: form.cookie.trim() };
    } else if (kind === 'sources') {
      if (!form.source_id?.trim() || !form.account_id || !form.title?.trim())
        throw new Error('请填写收藏夹 ID、显示名称并选择采集账号');
      payload = {
        ...(!editId.value ? { source_id: form.source_id.trim() } : {}),
        title: form.title.trim(),
        account_id: form.account_id,
        enabled: form.enabled,
        interval_minutes: form.interval_minutes,
        policy: parseObject(form.policy_text, '采集策略'),
      };
    } else if (kind === 'jobs') {
      if (!form.target_id) throw new Error('请选择或填写目标 ID');
      payload = {
        kind: form.kind,
        target_id: form.target_id,
        ...(form.account_id ? { account_id: form.account_id } : {}),
        policy: parseObject(form.policy_text, '任务策略'),
      };
    } else if (kind === 'storage') {
      if (!form.name?.trim()) throw new Error('请填写存储名称');
      payload = {
        name: form.name.trim(),
        kind: form.kind,
        config: parseObject(form.config_text, '存储配置'),
        is_default: form.is_default,
        enabled: form.enabled,
      };
      if (form.credentials_text?.trim())
        payload.credentials = parseObject(form.credentials_text, '存储凭据');
    } else if (kind === 'users') {
      if (!editId.value && (!form.username?.trim() || form.password?.length < 12))
        throw new Error('请填写用户名和至少 12 位密码');
      payload = editId.value
        ? { role: form.role, disabled: form.disabled }
        : { username: form.username.trim(), password: form.password, role: form.role };
    } else if (kind === 'videos')
      payload = {
        title_override: form.title_override || null,
        description_override: form.description_override || null,
        notes: form.notes,
        tags: tags(form.tags_text),
        starred: form.starred,
      };
    else if (kind === 'creators')
      payload = {
        alias: form.alias || null,
        description_override: form.description_override || null,
        notes: form.notes,
        tags: tags(form.tags_text),
      };
    else if (kind === 'migrate') {
      if (!form.target_profile_id || form.target_profile_id === editId.value)
        throw new Error('请选择不同的目标位置');
      payload = { target_profile_id: form.target_profile_id };
      if (form.asset_ids?.trim()) payload.asset_ids = tags(form.asset_ids);
      const d = await write(`/admin/storage/${editId.value}/migrate`, payload);
      feedback.value = d;
      dialog.value = false;
      ElMessage.success('迁移请求已提交，请在任务中心查看结果');
      await load(page.value);
      return;
    } else throw new Error('未支持的操作');
    const d = await write(
      `/admin/${kind}${editId.value ? `/${editId.value}` : ''}`,
      payload,
      editId.value ? 'PATCH' : 'POST',
    );
    feedback.value = d;
    form.cookie = '';
    form.credentials_text = '';
    form.password = '';
    dialog.value = false;
    ElMessage.success(kind === 'jobs' ? '任务已创建' : '已保存');
    await load(page.value);
  } catch (e) {
    formError.value = errorText(e);
  } finally {
    submitting.value = false;
  }
}
async function action(path: string, label: string, body?: unknown, confirm = false) {
  if (confirm) {
    try {
      await ElMessageBox.confirm(`确认${label}？已经归档的内容会保留。`, label, {
        confirmButtonText: '确认',
        cancelButtonText: '返回',
        type: 'warning',
      });
    } catch {
      return;
    }
  }
  actionBusy.value = path;
  feedback.value = null;
  try {
    const result = await write(path, body);
    feedback.value = result;
    ElMessage.success(
      label === '测试存储连接' ? '存储探测已排队，请在任务中心查看结果' : '请求已提交',
    );
    await load(page.value);
  } catch (e) {
    ElMessage.error(errorText(e));
  } finally {
    actionBusy.value = '';
  }
}
async function migrate(row: Row) {
  resetForm();
  await loadChoices();
  editId.value = row.id;
  dialogKind.value = 'migrate';
  dialogTitle.value = `迁移：${row.name}`;
  Object.assign(form, { target_profile_id: '', asset_ids: '' });
  dialog.value = true;
}
async function saveSettings() {
  submitting.value = true;
  formError.value = '';
  try {
    const payload = {
      ...settingsOriginal.value,
      display: { ...settingsForm.display },
      ingest: { ...settingsForm.ingest },
      backup: { ...settingsForm.backup },
    };
    await write('/admin/settings', payload, 'PATCH');
    ElMessage.success('配置已保存');
    await loadDisplaySettings();
    await load();
  } catch (e) {
    formError.value = errorText(e);
  } finally {
    submitting.value = false;
  }
}
const can = (job: Row, action: string) =>
  (
    ({
      retry: ['failed', 'blocked', 'partial', 'cancelled'],
      pause: ['queued', 'running'],
      resume: ['paused'],
      cancel: ['queued', 'running', 'paused', 'blocked', 'partial'],
    }) as Record<string, string[]>
  )[action]?.includes(job.status);
watch(
  section,
  async () => {
    sequence++;
    rows.value = [];
    q.value = '';
    status.value = '';
    page.value = 1;
    feedback.value = null;
    dialog.value = false;
    clearInterval(timer);
    await load();
    if (['jobs', 'overview'].includes(section.value))
      timer = setInterval(() => {
        if (!document.hidden && !dialog.value && !actionBusy.value) void load(page.value, true);
      }, 10000);
    if (route.query.edit && ['videos', 'creators'].includes(section.value))
      await openEditor(section.value, { id: String(route.query.edit) });
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  sequence++;
  clearInterval(timer);
});
</script>
<template>
  <el-config-provider :locale="zhCn"
    ><div class="admin-layout">
      <aside class="admin-sidebar">
        <div class="admin-sidebar-label">管理后台</div>
        <nav aria-label="后台导航">
          <RouterLink
            v-for="(item, i) in menu"
            :key="item.id"
            :to="item.id === 'overview' ? '/admin' : `/admin/${item.id}`"
            :class="{
              selected: section === item.id,
              'new-group': i > 0 && item.group !== menu[i - 1]?.group,
            }"
            >{{ item.name }}</RouterLink
          >
        </nav>
        <RouterLink to="/" class="admin-return">← 返回视频库</RouterLink>
      </aside>
      <main class="admin-main">
        <div class="page-heading">
          <div>
            <h1>{{ heading }}</h1>
            <p class="muted">{{ descriptions[section] }}</p>
          </div>
          <div class="toolbar-actions">
            <el-button :loading="busy" @click="load(page)">刷新</el-button
            ><el-button
              v-if="
                ['accounts', 'sources', 'jobs', 'storage', 'users'].includes(section) && allowed
              "
              type="primary"
              @click="openEditor()"
              >添加{{
                (
                  {
                    accounts: '账号',
                    sources: '来源',
                    jobs: '任务',
                    storage: '位置',
                    users: '用户',
                  } as Row
                )[section]
              }}</el-button
            ><el-button
              v-if="section === 'backups'"
              type="primary"
              :loading="!!actionBusy"
              @click="action('/admin/backups', '创建备份')"
              >立即备份</el-button
            >
          </div>
        </div>
        <el-alert
          v-if="!allowed"
          title="当前账号不能访问此管理页面。"
          type="warning"
          :closable="false"
        /><el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
          class="mb"
        />
        <template v-if="allowed">
          <template v-if="section === 'overview'"
            ><div v-if="overview" class="overview-summary">
              <span
                >视频 <strong>{{ overview.stats?.videos ?? '—' }}</strong></span
              ><span
                >UP 主 <strong>{{ overview.stats?.creators ?? '—' }}</strong></span
              ><span
                >收藏来源 <strong>{{ overview.stats?.collections ?? '—' }}</strong></span
              ><span
                >资产 <strong>{{ bytes(overview.stats?.assets_bytes) }}</strong></span
              ><span
                >待处理任务 <strong>{{ overview.stats?.jobs_pending ?? '—' }}</strong></span
              >
            </div>
            <section class="admin-panel">
              <div class="section-heading">
                <h2>最近任务</h2>
                <RouterLink to="/admin/jobs">全部任务 →</RouterLink>
              </div>
              <el-table :data="overview?.jobs || []" empty-text="暂无任务" v-loading="busy"
                ><el-table-column label="任务"
                  ><template #default="{ row }">{{
                    jobNames[row.kind] || row.kind
                  }}</template></el-table-column
                ><el-table-column label="状态"
                  ><template #default="{ row }"
                    ><span :class="`status-dot ${row.status}`">{{
                      statusText(row.status)
                    }}</span></template
                  ></el-table-column
                ><el-table-column label="创建时间"
                  ><template #default="{ row }">{{
                    date(row.created_at)
                  }}</template></el-table-column
                ><el-table-column prop="error" label="最近错误" show-overflow-tooltip
              /></el-table>
            </section>
            <div class="overview-bottom">
              <section class="admin-panel">
                <h2>存储位置</h2>
                <p v-if="!overview?.storage?.length" class="muted">尚未配置存储位置。</p>
                <div v-for="item in overview?.storage || []" :key="item.id" class="list-line">
                  <strong>{{ item.name }}</strong
                  ><span>{{ item.kind }} · {{ item.enabled ? '启用' : '停用' }}</span>
                </div>
                <RouterLink to="/admin/storage">管理存储 →</RouterLink>
              </section>
              <section class="admin-panel">
                <h2>备份记录</h2>
                <p v-if="overview?.backup_protection?.snapshot_at" class="muted small">
                  最近完整恢复点：{{ date(overview.backup_protection.snapshot_at) }} ·
                  {{
                    overview.backup_protection.within_24_hours ? '在 24 小时内' : '已超过 24 小时'
                  }}
                </p>
                <p v-else class="inline-notice">尚无完整恢复点。</p>
                <p v-if="!overview?.backups?.length" class="muted">尚无备份记录。</p>
                <div v-for="item in overview?.backups || []" :key="item.id" class="list-line">
                  <span>{{ date(item.snapshot_at) }}</span
                  ><span>{{ statusText(item.status) }}</span>
                </div>
                <RouterLink to="/admin/backups">查看备份 →</RouterLink>
              </section>
            </div></template
          >
          <template v-else-if="section === 'settings'"
            ><el-form class="settings-form" label-position="top" v-loading="busy"
              ><section class="admin-panel">
                <h2>展示</h2>
                <el-form-item label="网站名称"
                  ><el-input
                    v-model="settingsForm.display.site_name"
                    maxlength="60" /></el-form-item
                ><el-form-item label="默认显示弹幕"
                  ><el-switch v-model="settingsForm.display.default_danmaku" />
                  <p class="field-help">
                    首次使用播放器时生效。浏览器已有的个人弹幕偏好优先。
                  </p></el-form-item
                >
              </section>
              <section class="admin-panel">
                <h2>采集默认策略</h2>
                <div class="form-two-columns">
                  <el-form-item label="目标画质"
                    ><el-select v-model="settingsForm.ingest.quality"
                      ><el-option
                        v-for="option in qualityOptions"
                        :key="option.value"
                        :value="option.value"
                        :label="option.label" /></el-select></el-form-item
                  ><el-form-item label="单轮请求预算"
                    ><el-input-number
                      v-model="settingsForm.ingest.request_budget"
                      :min="1"
                      :max="10000" /></el-form-item
                  ><el-form-item label="单视频下载上限（字节）"
                    ><el-input-number
                      v-model="settingsForm.ingest.max_download_bytes"
                      :min="1000000"
                      :max="500000000000"
                      :step="1000000000"
                      :controls="false"
                  /></el-form-item>
                </div>
                <div class="settings-switches">
                  <el-checkbox v-model="settingsForm.ingest.download_media">下载媒体</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.create_compatible_copy"
                    >生成浏览器兼容副本</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.prefer_h264"
                    >优先下载 H.264 编码</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.fetch_comments">采集评论</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.fetch_danmaku">采集弹幕</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.fetch_subtitles">采集字幕</el-checkbox
                  ><el-checkbox v-model="settingsForm.ingest.include_auto_subtitles"
                    >包括自动字幕</el-checkbox
                  >
                </div>
                <p class="field-help">
                  画质以账号实际可见且成功归档的版本为准。兼容副本保留原档，并按需生成适合浏览器的播放文件，会额外占用空间和处理时间；并非所有
                  HDR 格式都能转换。优先 H.264 可能限制可选画质。策略应用于新任务。
                </p>
              </section>
              <section class="admin-panel">
                <h2>备份</h2>
                <el-form-item label="启用定期备份"
                  ><el-switch v-model="settingsForm.backup.enabled" /></el-form-item
                ><el-form-item label="独立备份目录"
                  ><el-input
                    v-model="settingsForm.backup.destination"
                    placeholder="服务器上的独立备份路径" /></el-form-item
                ><el-form-item label="密钥标识 key_id"
                  ><el-input
                    v-model="settingsForm.backup.key_id"
                    placeholder="与服务端配置的备份密钥标识一致"
                /></el-form-item>
                <div class="form-two-columns">
                  <el-form-item label="备份周期（小时）"
                    ><el-input-number
                      v-model="settingsForm.backup.interval_hours"
                      :min="1"
                      :max="720" /></el-form-item
                  ><el-form-item label="保留天数（策略记录）"
                    ><el-input-number
                      v-model="settingsForm.backup.retention_days"
                      :min="1"
                      :max="3650"
                  /></el-form-item>
                </div>
                <el-alert
                  title="备份目录必须独立于主媒体目录。加密密钥通过 TREASURE_BACKUP_KEY 或 TREASURE_BACKUP_KEY_FILE 配置并单独保管。备份目标为独立挂载目录，可使用 NAS；此处不配置云备份目标。当前版本不自动删除备份。"
                  type="info"
                  :closable="false"
                />
              </section>
              <el-alert
                v-if="formError"
                :title="formError"
                type="error"
                :closable="false"
                class="mb"
              /><el-button type="primary" :loading="submitting" @click="saveSettings"
                >保存配置</el-button
              ></el-form
            ></template
          >
          <template v-else>
            <div v-if="['videos', 'creators', 'jobs'].includes(section)" class="admin-filter">
              <el-input
                v-if="section !== 'jobs'"
                v-model="q"
                :placeholder="
                  section === 'videos' ? '搜索视频标题或 BV 号' : '搜索昵称、UID 或简介'
                "
                clearable
                @keyup.enter="load()"
                @clear="load()"
              /><el-select v-else v-model="status" placeholder="所有状态" clearable @change="load()"
                ><el-option
                  v-for="s in statuses"
                  :key="s"
                  :label="statusText(s)"
                  :value="s" /></el-select
              ><el-button @click="load()">筛选</el-button>
            </div>
            <el-alert
              v-if="section === 'accounts'"
              title="Cookie 加密保存，需要服务端已配置加密密钥。验证只创建任务，最终结果请查看任务中心。"
              type="info"
              :closable="false"
              class="mb"
            />
            <el-alert
              v-if="section === 'backups'"
              title="立即备份会创建后台任务。请先在系统配置填写独立目录和密钥标识；缺少条件时任务会明确失败。恢复须在隔离环境通过服务端流程执行。"
              type="info"
              :closable="false"
              class="mb"
            />
            <el-table
              :data="rows"
              v-loading="busy"
              empty-text="暂无记录"
              row-key="id"
              class="admin-table"
            >
              <el-table-column
                v-if="['jobs', 'backups', 'audit', 'storage'].includes(section)"
                type="expand"
                ><template #default="{ row }"
                  ><div class="record-detail">
                    <template v-if="section === 'jobs'"
                      ><h3>检查点</h3>
                      <pre>{{ JSON.stringify(row.checkpoint || {}, null, 2) }}</pre>
                      <h3>执行结果</h3>
                      <div
                        v-if="row.kind === 'probe_storage' && row.result?.status"
                        class="probe-results"
                      >
                        <p>
                          存储探测：{{
                            row.result.status === 'passed' ? '已通过以下检查' : '未通过'
                          }}
                        </p>
                        <div v-for="(value, key) in row.result.checks || {}" :key="String(key)">
                          {{
                            (
                              {
                                put: '写入',
                                head: '对象信息',
                                sha256_readback: '读取与哈希校验',
                                single_range: '范围读取',
                              } as Row
                            )[key] || key
                          }}：{{ value === true ? '通过' : value === false ? '失败' : '未测试' }}
                        </div>
                        <p class="muted small">
                          分片上传：{{
                            row.result.multipart === 'not_tested' ? '未测试' : '请查看详细结果'
                          }}
                          · 浏览器跨域：{{
                            row.result.browser_cors === 'not_tested' ? '未测试' : '请查看详细结果'
                          }}
                        </p>
                      </div>
                      <pre>{{ JSON.stringify(row.result || {}, null, 2) }}</pre>
                      <p v-if="row.error" class="form-error">{{ row.error }}</p>
                      <p class="muted">任务 ID：{{ row.id }}</p></template
                    ><template v-else>
                      <pre>{{
                        JSON.stringify(
                          section === 'storage'
                            ? { config: row.config, has_credentials: row.has_credentials }
                            : row,
                          null,
                          2,
                        )
                      }}</pre>
                    </template>
                  </div></template
                ></el-table-column
              >
              <template v-if="section === 'accounts'"
                ><el-table-column prop="name" label="账号名称" min-width="150" /><el-table-column
                  prop="uid"
                  label="UID"
                  min-width="130"
                /><el-table-column label="状态" min-width="100"
                  ><template #default="{ row }">{{
                    statusText(row.status)
                  }}</template></el-table-column
                ><el-table-column label="最近验证" min-width="180"
                  ><template #default="{ row }">{{
                    date(row.last_verified_at)
                  }}</template></el-table-column
                ><el-table-column label="操作" width="110"
                  ><template #default="{ row }"
                    ><el-button
                      size="small"
                      :loading="actionBusy === `/admin/accounts/${row.id}/verify`"
                      @click="action(`/admin/accounts/${row.id}/verify`, '验证账号')"
                      >验证账号</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'sources'"
                ><el-table-column prop="title" label="收藏夹" min-width="170" /><el-table-column
                  prop="source_id"
                  label="来源 ID"
                  min-width="130"
                /><el-table-column label="周期" width="100"
                  ><template #default="{ row }"
                    >{{ row.interval_minutes }} 分钟</template
                  ></el-table-column
                ><el-table-column label="状态" width="80"
                  ><template #default="{ row }">{{
                    row.enabled ? '启用' : '停用'
                  }}</template></el-table-column
                ><el-table-column label="最近扫描" min-width="180"
                  ><template #default="{ row }">{{
                    date(row.last_scan_at)
                  }}</template></el-table-column
                ><el-table-column label="操作" width="190"
                  ><template #default="{ row }"
                    ><el-button size="small" @click="openEditor('sources', row)">编辑</el-button
                    ><el-button
                      size="small"
                      :loading="actionBusy === `/admin/sources/${row.id}/scan`"
                      @click="action(`/admin/sources/${row.id}/scan`, '扫描收藏夹')"
                      >立即扫描</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'jobs'"
                ><el-table-column label="任务" min-width="140"
                  ><template #default="{ row }"
                    >{{ jobNames[row.kind] || row.kind
                    }}<small class="table-subtitle">{{ row.id }}</small></template
                  ></el-table-column
                ><el-table-column label="状态" min-width="100"
                  ><template #default="{ row }"
                    ><span :class="`status-dot ${row.status}`">{{
                      statusText(row.status)
                    }}</span></template
                  ></el-table-column
                ><el-table-column label="尝试" width="70" prop="attempts" /><el-table-column
                  label="创建时间"
                  min-width="165"
                  ><template #default="{ row }">{{
                    date(row.created_at)
                  }}</template></el-table-column
                ><el-table-column
                  label="错误"
                  prop="error"
                  min-width="180"
                  show-overflow-tooltip
                /><el-table-column label="操作" width="210"
                  ><template #default="{ row }"
                    ><el-button
                      v-if="can(row, 'retry')"
                      size="small"
                      @click="action(`/admin/jobs/${row.id}/retry`, '重试任务')"
                      >重试</el-button
                    ><el-button
                      v-if="can(row, 'pause')"
                      size="small"
                      @click="action(`/admin/jobs/${row.id}/pause`, '暂停任务')"
                      >暂停</el-button
                    ><el-button
                      v-if="can(row, 'resume')"
                      size="small"
                      @click="action(`/admin/jobs/${row.id}/resume`, '恢复任务')"
                      >恢复</el-button
                    ><el-button
                      v-if="can(row, 'cancel')"
                      size="small"
                      type="danger"
                      plain
                      @click="action(`/admin/jobs/${row.id}/cancel`, '取消任务', undefined, true)"
                      >取消</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'videos'"
                ><el-table-column label="视频" min-width="290"
                  ><template #default="{ row }"
                    ><RouterLink :to="`/videos/${row.id}`">{{ row.title }}</RouterLink
                    ><small class="table-subtitle">{{ row.bvid }}</small></template
                  ></el-table-column
                ><el-table-column label="标签" min-width="130"
                  ><template #default="{ row }">{{
                    row.tags?.join('、') || '—'
                  }}</template></el-table-column
                ><el-table-column label="归档状态" min-width="110"
                  ><template #default="{ row }">{{
                    statusText(row.capture_status)
                  }}</template></el-table-column
                ><el-table-column label="星标" width="65"
                  ><template #default="{ row }">{{
                    row.starred ? '★' : '—'
                  }}</template></el-table-column
                ><el-table-column label="操作" width="105"
                  ><template #default="{ row }"
                    ><el-button size="small" @click="openEditor('videos', row)"
                      >编辑标注</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'creators'"
                ><el-table-column label="UP 主" min-width="220"
                  ><template #default="{ row }"
                    ><RouterLink :to="`/creators/${row.id}`">{{ row.name }}</RouterLink
                    ><small class="table-subtitle">UID {{ row.uid }}</small></template
                  ></el-table-column
                ><el-table-column
                  prop="description"
                  label="简介"
                  min-width="220"
                  show-overflow-tooltip
                /><el-table-column
                  prop="saved_count"
                  label="已保存视频"
                  width="110"
                /><el-table-column label="标签" min-width="100"
                  ><template #default="{ row }">{{
                    row.tags?.join('、') || '—'
                  }}</template></el-table-column
                ><el-table-column label="操作" width="100"
                  ><template #default="{ row }"
                    ><el-button size="small" @click="openEditor('creators', row)"
                      >编辑资料</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'storage'"
                ><el-table-column prop="name" label="位置" min-width="160" /><el-table-column
                  prop="kind"
                  label="类型"
                  width="90"
                /><el-table-column label="状态" width="150"
                  ><template #default="{ row }"
                    >{{ row.enabled ? '启用' : '停用'
                    }}{{ row.is_default ? ' · 默认位置' : '' }}</template
                  ></el-table-column
                ><el-table-column label="凭据" width="110"
                  ><template #default="{ row }">{{
                    row.has_credentials ? '已设置' : '未设置'
                  }}</template></el-table-column
                ><el-table-column label="操作" min-width="265"
                  ><template #default="{ row }"
                    ><el-button size="small" @click="openEditor('storage', row)">编辑</el-button
                    ><el-button
                      size="small"
                      :loading="actionBusy === `/admin/storage/${row.id}/probe`"
                      @click="action(`/admin/storage/${row.id}/probe`, '测试存储连接')"
                      >探测能力</el-button
                    ><el-button size="small" @click="migrate(row)">迁移</el-button></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'backups'"
                ><el-table-column prop="id" label="备份 ID" min-width="230" /><el-table-column
                  label="状态"
                  width="100"
                  ><template #default="{ row }">{{
                    statusText(row.status)
                  }}</template></el-table-column
                ><el-table-column label="快照时间" min-width="170"
                  ><template #default="{ row }">{{
                    date(row.snapshot_at)
                  }}</template></el-table-column
                ><el-table-column label="完成时间" min-width="170"
                  ><template #default="{ row }">{{
                    date(row.completed_at)
                  }}</template></el-table-column
                ><el-table-column prop="error" label="错误" min-width="150" show-overflow-tooltip
              /></template>
              <template v-else-if="section === 'users'"
                ><el-table-column prop="username" label="用户名" min-width="170" /><el-table-column
                  label="权限"
                  min-width="110"
                  ><template #default="{ row }">{{
                    ({ admin: '管理员', editor: '内容编辑', reader: '只读用户' } as Row)[
                      row.role
                    ] || row.role
                  }}</template></el-table-column
                ><el-table-column label="状态" min-width="100"
                  ><template #default="{ row }">{{
                    row.disabled ? '已停用' : '启用'
                  }}</template></el-table-column
                ><el-table-column label="操作" width="120"
                  ><template #default="{ row }"
                    ><el-button size="small" @click="openEditor('users', row)"
                      >编辑权限</el-button
                    ></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'audit'"
                ><el-table-column label="时间" min-width="175"
                  ><template #default="{ row }">{{
                    date(row.created_at)
                  }}</template></el-table-column
                ><el-table-column prop="action" label="操作" min-width="140" /><el-table-column
                  prop="entity_type"
                  label="对象类型"
                  min-width="110" /><el-table-column
                  prop="entity_id"
                  label="对象 ID"
                  min-width="200" /><el-table-column
                  prop="actor_id"
                  label="操作人 ID"
                  min-width="200"
              /></template> </el-table
            ><el-pagination
              v-if="total > 20"
              class="admin-pagination"
              layout="total, prev, pager, next"
              :page-size="20"
              :total="total"
              :current-page="page"
              @current-change="load"
            />
          </template>
          <details v-if="feedback" class="action-feedback" open>
            <summary>最近操作的返回结果</summary>
            <p v-if="feedback.kind && feedback.id">
              <RouterLink to="/admin/jobs">在任务中心查看执行状态 →</RouterLink>
            </p>
            <pre>{{ JSON.stringify(feedback, null, 2) }}</pre>
          </details>
        </template>
      </main>
    </div>
    <el-dialog
      v-model="dialog"
      :title="dialogTitle"
      width="min(680px, 94vw)"
      :close-on-click-modal="!submitting"
      :close-on-press-escape="!submitting"
      @closed="resetForm"
      ><el-form label-position="top" @submit.prevent="submit">
        <template v-if="dialogKind === 'accounts'"
          ><el-form-item label="账号名称" required
            ><el-input v-model="form.name" placeholder="用于区分采集账号" /></el-form-item
          ><el-form-item label="Cookie" required
            ><el-input
              v-model="form.cookie"
              type="password"
              show-password
              autocomplete="off"
              placeholder="粘贴你授权使用的账号 Cookie"
            />
            <p class="field-help">不会写入浏览器存储，保存后清空输入。</p></el-form-item
          ></template
        >
        <template v-if="dialogKind === 'sources'"
          ><el-form-item label="收藏夹 ID" required
            ><el-input
              v-model="form.source_id"
              :disabled="!!editId"
              placeholder="收藏夹地址中的 fid 或媒体 ID" /></el-form-item
          ><el-form-item label="显示名称" required><el-input v-model="form.title" /></el-form-item
          ><el-form-item label="采集账号" required
            ><el-select v-model="form.account_id" placeholder="选择已配置账号"
              ><el-option
                v-for="account in accounts"
                :key="account.id"
                :label="account.name"
                :value="account.id"
            /></el-select>
            <p v-if="!accounts.length" class="field-help">请先添加 B 站采集账号。</p></el-form-item
          ><el-form-item label="扫描周期（分钟）"
            ><el-input-number
              v-model="form.interval_minutes"
              :min="15"
              :max="525600" /></el-form-item
          ><el-form-item label="启用定期扫描"><el-switch v-model="form.enabled" /></el-form-item
          ><el-form-item label="来源策略 JSON"
            ><el-input v-model="form.policy_text" type="textarea" :rows="5" />
            <p class="field-help">
              留空对象使用系统策略；可覆盖 quality、request_budget、fetch_comments 等采集配置。
            </p></el-form-item
          ></template
        >
        <template v-if="dialogKind === 'jobs'"
          ><el-form-item label="任务类型"
            ><el-select v-model="form.kind" @change="loadTargets"
              ><el-option
                v-for="kind in [
                  'archive_video',
                  'refresh_comments',
                  'scan_collection',
                  'verify_account',
                ]"
                :key="kind"
                :label="jobNames[kind]"
                :value="kind" /></el-select></el-form-item
          ><el-form-item label="目标" required
            ><el-select
              v-model="form.target_id"
              filterable
              allow-create
              placeholder="选择目标，或输入本站目标 ID"
              ><el-option
                v-for="item in targets"
                :key="item.id"
                :value="item.id"
                :label="item.title || item.name || item.id"
            /></el-select>
            <p class="field-help">
              此处为本站记录 ID；扫描来源请先在收藏来源中添加收藏夹。
            </p></el-form-item
          ><el-form-item label="采集账号"
            ><el-select v-model="form.account_id" clearable
              ><el-option
                v-for="account in accounts"
                :key="account.id"
                :label="account.name"
                :value="account.id" /></el-select></el-form-item
          ><el-form-item label="任务策略 JSON"
            ><el-input v-model="form.policy_text" type="textarea" :rows="5" /></el-form-item
        ></template>
        <template v-if="dialogKind === 'storage'"
          ><el-form-item label="存储名称" required><el-input v-model="form.name" /></el-form-item
          ><el-form-item label="类型"
            ><el-select v-model="form.kind"
              ><el-option value="local" label="本地目录" /><el-option
                value="s3"
                label="S3 兼容存储" /><el-option
                value="oss"
                label="阿里云 OSS" /></el-select></el-form-item
          ><el-form-item label="位置配置 JSON" required
            ><el-input v-model="form.config_text" type="textarea" :rows="7" />
            <p class="field-help">
              本地 root 须位于部署时挂载的媒体根目录内；留空对象使用服务器默认目录。S3 / OSS 支持
              bucket、endpoint、public_endpoint、region、prefix；S3 另支持 addressing_style。
            </p></el-form-item
          ><el-form-item label="凭据 JSON"
            ><el-input
              v-model="form.credentials_text"
              type="textarea"
              :rows="3"
              autocomplete="off"
              :placeholder="editId ? '留空保留现有凭据；凭据不会回显' : '仅在需要云凭据时填写'"
            />
            <p class="field-help">
              S3：access_key_id、secret_access_key、session_token（可选）；OSS：access_key_id、access_key_secret、security_token（可选）。服务端需配置加密密钥。
            </p></el-form-item
          >
          <div class="settings-switches">
            <el-checkbox v-model="form.is_default">设为默认写入位置</el-checkbox
            ><el-checkbox v-model="form.enabled">启用</el-checkbox>
          </div></template
        >
        <template v-if="['sources', 'jobs'].includes(dialogKind)"
          ><div class="form-two-columns">
            <el-form-item label="浏览器兼容副本"
              ><el-select
                :model-value="policyOverride('create_compatible_copy')"
                @update:model-value="
                  (value) => setPolicyOverride('create_compatible_copy', String(value))
                "
                ><el-option value="inherit" label="使用系统设置" /><el-option
                  value="true"
                  label="按需生成兼容副本" /><el-option
                  value="false"
                  label="仅保存归档原档" /></el-select></el-form-item
            ><el-form-item label="优先 H.264"
              ><el-select
                :model-value="policyOverride('prefer_h264')"
                @update:model-value="(value) => setPolicyOverride('prefer_h264', String(value))"
                ><el-option value="inherit" label="使用系统设置" /><el-option
                  value="true"
                  label="优先 H.264 编码" /><el-option
                  value="false"
                  label="不限制编码偏好" /></el-select
            ></el-form-item>
          </div>
          <p class="field-help">
            兼容副本保留原档，可能增加转码时间和占用空间；未支持的 HDR
            转换会记录具体状态。上述选择会同步到策略 JSON。
          </p></template
        ><template v-if="dialogKind === 'migrate'"
          ><el-alert
            title="迁移会复制并校验资产。默认位置的切换不会替代迁移。"
            type="info"
            :closable="false"
            class="mb" /><el-form-item label="目标位置" required
            ><el-select v-model="form.target_profile_id"
              ><el-option
                v-for="item in storages.filter((s) => s.id !== editId && s.enabled)"
                :key="item.id"
                :value="item.id"
                :label="item.name" /></el-select></el-form-item
          ><el-form-item label="资产 ID（可选）"
            ><el-input
              v-model="form.asset_ids"
              type="textarea"
              :rows="3"
              placeholder="逗号或换行分隔；留空迁移此位置全部资产" /></el-form-item
        ></template>
        <template v-if="dialogKind === 'users'"
          ><el-form-item label="用户名" required
            ><el-input
              v-model="form.username"
              :disabled="!!editId"
              autocomplete="off" /></el-form-item
          ><el-form-item v-if="!editId" label="初始密码" required
            ><el-input
              v-model="form.password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="至少 12 位" /></el-form-item
          ><el-form-item label="权限"
            ><el-select v-model="form.role" :disabled="editId === session.user?.id"
              ><el-option value="reader" label="只读：浏览与播放" /><el-option
                value="editor"
                label="编辑：浏览与整理内容" /><el-option
                value="admin"
                label="管理员：全部管理功能" /></el-select></el-form-item
          ><el-form-item v-if="editId" label="停用账号"
            ><el-switch
              v-model="form.disabled"
              :disabled="editId === session.user?.id" /></el-form-item
        ></template>
        <template v-if="dialogKind === 'videos'"
          ><p class="field-help">来源标题：{{ form.source_title }}</p>
          <el-form-item label="本地标题（留空恢复来源）"
            ><el-input v-model="form.title_override" /></el-form-item
          ><el-form-item label="本地简介（留空恢复来源）"
            ><el-input
              v-model="form.description_override"
              type="textarea"
              :rows="4" /></el-form-item
          ><el-form-item label="收藏笔记"
            ><el-input v-model="form.notes" type="textarea" :rows="4" /></el-form-item
          ><el-form-item label="标签（逗号分隔）"
            ><el-input v-model="form.tags_text" /></el-form-item
          ><el-checkbox v-model="form.starred">星标视频</el-checkbox></template
        >
        <template v-if="dialogKind === 'creators'"
          ><p class="field-help">平台昵称：{{ form.source_name }}</p>
          <el-form-item label="本地别名（留空恢复来源）"
            ><el-input v-model="form.alias" /></el-form-item
          ><el-form-item label="本地简介（留空恢复来源）"
            ><el-input
              v-model="form.description_override"
              type="textarea"
              :rows="4" /></el-form-item
          ><el-form-item label="整理笔记"
            ><el-input v-model="form.notes" type="textarea" :rows="4" /></el-form-item
          ><el-form-item label="标签（逗号分隔）"
            ><el-input v-model="form.tags_text" /></el-form-item
        ></template>
        <el-alert
          v-if="formError"
          :title="formError"
          type="error"
          :closable="false"
          class="mt"
        /> </el-form
      ><template #footer
        ><el-button :disabled="submitting" @click="dialog = false">取消</el-button
        ><el-button type="primary" :loading="submitting" @click="submit">{{
          dialogKind === 'jobs' ? '创建任务' : dialogKind === 'migrate' ? '提交迁移' : '保存'
        }}</el-button></template
      ></el-dialog
    ></el-config-provider
  >
</template>
