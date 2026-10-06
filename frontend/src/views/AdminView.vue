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
import { api, write, query, errorText, statusText, date, bytes, session } from '../api';
import type { Row, Page } from '../types';
import AdminSettings from '../components/admin/AdminSettings.vue';
import StorageReplicas from '../components/admin/StorageReplicas.vue';
import SourceMonitor from '../components/admin/SourceMonitor.vue';
import StorageFields from '../components/admin/StorageFields.vue';
import StorageProbeResult from '../components/admin/StorageProbeResult.vue';
import OperationResult from '../components/admin/OperationResult.vue';
import JobDetails from '../components/admin/JobDetails.vue';
import IngestPolicyFields from '../components/admin/IngestPolicyFields.vue';
import VideoEditorFields from '../components/admin/VideoEditorFields.vue';
import DeletionDialog from '../components/admin/DeletionDialog.vue';
import UiIcon from '../components/UiIcon.vue';
import UserscriptIntegration from '../components/UserscriptIntegration.vue';
import { videoEditorDraft, videoEditorPayload } from '../utils/videoEditor';
import { storageDraft, storagePayload, storageKindName } from '../utils/storageForm';
import { jobNames, jobPhase, jobStatusLabel, mediaCaptureLabel } from '../utils/jobDisplay';
import { createScopedInterval } from '../utils/scopedInterval';
const route = useRoute();
const sections = [
  { id: 'overview', name: '总览', group: '运行' },
  { id: 'accounts', name: 'B 站账号', group: '采集' },
  { id: 'sources', name: '自动备份', group: '采集' },
  { id: 'browser', name: '浏览器采集', group: '采集' },
  { id: 'jobs', name: '任务中心', group: '采集' },
  { id: 'videos', name: '视频资料', group: '内容' },
  { id: 'creators', name: 'UP 主整理', group: '内容' },
  { id: 'storage', name: '存储位置', group: '维护' },
  { id: 'replicas', name: '副本与分发', group: '维护' },
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
  sources: '关注 UP 主的新投稿和收藏夹更新。',
  browser: '在 B 站勾选视频，交给自己的归档库保存。',
  jobs: '下载、同步与整理进度。',
  videos: '整理标题、封面与标签。',
  creators: '管理已保存的 UP 主与投稿。',
  storage: '管理本地目录、对象存储与云盘，设置视频的写入位置和播放方式。',
  replicas: '管理各个存储位置的视频副本。',
  backups: '查看备份记录与恢复验证结果。',
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
const savedVideo = ref('');
const deletionTarget = ref<{ kind: 'videos' | 'creators'; id: string } | null>(null);
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
const storageForm = ref(storageDraft());
const operationOpen = ref(false);
const probeOpen = ref(false),
  probeResult = ref<Row | null>(null),
  probeName = ref('');
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
const settingsRevision = ref(0);
let sequence = 0;
let editorSequence = 0;
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
    } else if (['settings', 'replicas', 'sources', 'browser'].includes(section.value)) {
      settingsRevision.value++;
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
function policyValues() {
  return Object.fromEntries(
    Object.entries(form.policy || {}).filter(([, value]) => value !== '' && value != null),
  );
}
function showOperation(result: Row) {
  if (result?.id && result?.kind && result?.status) {
    feedback.value = result;
    operationOpen.value = true;
  }
}
const storageLabels: Record<string, string> = {
  root: '媒体目录',
  bucket: '存储桶',
  endpoint: '服务端地址',
  public_endpoint: '播放地址',
  public_base_url: '自定义域名 / CDN',
  private_bucket: '私有空间',
  delivery_mode: '播放传输方式',
  drive_id: '云盘 / 文档库',
  site_id: 'SharePoint 站点',
  tenant_id: 'Microsoft 租户',
  region: '区域',
  prefix: '对象前缀',
  addressing_style: '桶寻址',
  read_priority: '读取优先级',
  part_size: '分片大小（字节）',
};
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
  editorSequence++;
  Object.keys(form).forEach((k) => delete form[k]);
  formError.value = '';
  storageForm.value.credentials = {};
}
async function openEditor(kind = section.value, row?: Row) {
  resetForm();
  const editorTicket = editorSequence;
  editId.value = row?.id || '';
  dialogKind.value = kind;
  dialogTitle.value = `${row ? '编辑' : '添加'}${({ accounts: 'B 站账号', sources: '收藏来源', jobs: '任务', storage: '存储位置', users: '本站用户', videos: '视频资料', creators: 'UP 主资料' } as Row)[kind] || ''}`;
  if (['accounts', 'sources', 'jobs', 'storage', 'users'].includes(kind)) await loadChoices();
  if (editorTicket !== editorSequence) return;
  if (kind === 'accounts') Object.assign(form, { name: row?.name || '', cookie: '' });
  if (kind === 'sources')
    Object.assign(form, {
      source_id: '',
      title: '',
      account_id: accounts.value[0]?.id || '',
      enabled: false,
      interval_minutes: 1440,
      policy: {},
      ...row,
      policy_text_override: undefined,
    });
  if (kind === 'sources' && row) form.policy = { ...row.policy };
  if (kind === 'jobs') {
    Object.assign(form, {
      kind: 'archive_video',
      target_id: '',
      account_id: accounts.value[0]?.id || '',
      policy: {},
    });
    await loadTargets();
  }
  if (kind === 'storage') storageForm.value = storageDraft(row);
  if (kind === 'users')
    Object.assign(form, { username: '', password: '', role: 'reader', disabled: false, ...row });
  if (kind === 'videos') {
    try {
      const d = await api<Row>(`/videos/${row!.id}`);
      if (editorTicket !== editorSequence) return;
      Object.assign(form, videoEditorDraft(d));
    } catch (e) {
      if (editorTicket === editorSequence) ElMessage.error(errorText(e));
      return;
    }
  }
  if (kind === 'creators') {
    try {
      const d = await api<Row>(`/creators/${row!.id}`);
      if (editorTicket !== editorSequence) return;
      Object.assign(form, {
        source_name: d.source_name,
        alias: d.alias ?? (d.source_name !== d.name ? d.name : ''),
        description_override: d.description_override ?? '',
        notes: d.notes || '',
        tags_text: (d.tags || []).join('，'),
      });
    } catch (e) {
      if (editorTicket === editorSequence) ElMessage.error(errorText(e));
      return;
    }
  }
  if (editorTicket === editorSequence) dialog.value = true;
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
      if (!form.name?.trim() || (!editId.value && !form.cookie?.trim()))
        throw new Error('请填写账号名称和 Cookie');
      payload = {
        name: form.name.trim(),
        ...(form.cookie?.trim() ? { cookie: form.cookie.trim() } : {}),
      };
    } else if (kind === 'sources') {
      if (!form.source_id?.trim() || !form.account_id || !form.title?.trim())
        throw new Error('请填写收藏夹 ID、显示名称并选择采集账号');
      payload = {
        ...(!editId.value ? { source_id: form.source_id.trim() } : {}),
        title: form.title.trim(),
        account_id: form.account_id,
        enabled: form.enabled,
        interval_minutes: form.interval_minutes,
        policy: policyValues(),
      };
    } else if (kind === 'jobs') {
      if (!form.target_id) throw new Error('请选择或填写目标 ID');
      payload = {
        kind: form.kind,
        target_id: form.target_id,
        ...(form.account_id ? { account_id: form.account_id } : {}),
        policy: policyValues(),
      };
    } else if (kind === 'storage') {
      payload = storagePayload(storageForm.value);
    } else if (kind === 'users') {
      if (!editId.value && (!form.username?.trim() || form.password?.length < 12))
        throw new Error('请填写用户名和至少 12 位密码');
      payload = editId.value
        ? { role: form.role, disabled: form.disabled }
        : { username: form.username.trim(), password: form.password, role: form.role };
    } else if (kind === 'videos')
      payload = videoEditorPayload(form as ReturnType<typeof videoEditorDraft>);
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
      showOperation(d);
      dialog.value = false;
      ElMessage.success('迁移请求已提交');
      await load(page.value);
      return;
    } else throw new Error('未支持的操作');
    const d = await write(
      `/admin/${kind}${editId.value ? `/${editId.value}` : ''}`,
      payload,
      editId.value ? 'PATCH' : 'POST',
    );
    showOperation(d);
    form.cookie = '';
    storageForm.value.credentials = {};
    form.password = '';
    dialog.value = false;
    if (kind === 'videos') savedVideo.value = d.title || form.title;
    ElMessage.success(
      kind === 'jobs' ? '任务已创建' : kind === 'videos' ? '视频资料已保存' : '已保存',
    );
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
    showOperation(result);
    ElMessage.success('请求已提交');
    await load(page.value);
  } catch (e) {
    ElMessage.error(errorText(e));
  } finally {
    actionBusy.value = '';
  }
}
async function probeStorage(row: Row) {
  if (actionBusy.value) return;
  const path = `/admin/storage/${row.id}/probe`;
  actionBusy.value = path;
  try {
    const result = await api<Row>(path, { method: 'POST', signal: AbortSignal.timeout(90000) });
    probeResult.value = result;
    probeName.value = row.name;
    probeOpen.value = true;
    await load(page.value, true);
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
  async (_, __, onCleanup) => {
    const startPolling = createScopedInterval(onCleanup);
    let disposed = false;
    onCleanup(() => {
      disposed = true;
    });
    sequence++;
    editorSequence++;
    rows.value = [];
    q.value = '';
    status.value = '';
    page.value = 1;
    feedback.value = null;
    savedVideo.value = '';
    deletionTarget.value = null;
    operationOpen.value = false;
    dialog.value = false;
    await load();
    if (disposed) return;
    if (['jobs', 'overview'].includes(section.value))
      startPolling(() => {
        if (!document.hidden && !dialog.value && !actionBusy.value) void load(page.value, true);
      }, 10000);
    if (route.query.edit && ['videos', 'creators'].includes(section.value))
      await openEditor(section.value, { id: String(route.query.edit) });
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  sequence++;
  editorSequence++;
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
          <div v-if="!['sources', 'browser'].includes(section)" class="toolbar-actions">
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
          <el-alert
            v-if="section === 'videos' && savedVideo"
            :title="`已保存《${savedVideo}》的资料`"
            type="success"
            show-icon
            class="mb"
            @close="savedVideo = ''"
          />
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
                  ><span
                    >{{ storageKindName(item.kind) }} · {{ item.enabled ? '启用' : '停用' }}</span
                  >
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
          <AdminSettings v-else-if="section === 'settings'" :key="settingsRevision" />
          <StorageReplicas v-else-if="section === 'replicas'" :key="settingsRevision" />
          <SourceMonitor v-else-if="section === 'sources'" />
          <UserscriptIntegration v-else-if="section === 'browser'" />
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
                    <JobDetails
                      v-if="section === 'jobs'"
                      :job="row"
                      @retry-media="
                        (id, kind) =>
                          action(
                            `/admin/jobs/${id}/${kind}`,
                            kind === 'resume' ? '恢复视频下载' : '重试视频下载',
                          )
                      "
                    />
                    <dl v-else-if="section === 'storage'" class="record-fields">
                      <template v-for="(label, key) in storageLabels" :key="key"
                        ><template v-if="row.config?.[key] != null"
                          ><dt>{{ label }}</dt>
                          <dd>{{ row.config[key] }}</dd></template
                        ></template
                      >
                      <dt>访问凭据</dt>
                      <dd>{{ row.has_credentials ? '已配置（不回显）' : '未配置' }}</dd>
                    </dl>
                    <template v-else-if="section === 'backups'">
                      <p>备份状态：{{ statusText(row.status) }}</p>
                      <p>恢复点：{{ date(row.snapshot_at) }}</p>
                      <p>完成时间：{{ date(row.completed_at) }}</p>
                      <p v-if="row.error" class="form-error">{{ row.error }}</p>
                      <p class="muted">恢复演练需在隔离环境中执行，完成备份不等于已验证恢复。</p>
                    </template>
                    <details v-else>
                      <summary>审计详情</summary>
                      <pre>{{ JSON.stringify(row, null, 2) }}</pre>
                    </details>
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
                ><el-table-column label="请求与冷却" min-width="220"
                  ><template #default="{ row }"
                    ><span v-if="row.cooldown_until">冷却至 {{ date(row.cooldown_until) }}</span
                    ><span v-else>无冷却记录</span
                    ><small class="table-subtitle" v-if="row.next_request_at"
                      >下次请求 {{ date(row.next_request_at) }}</small
                    ><small class="table-subtitle" v-if="row.next_video_at"
                      >下个视频 {{ date(row.next_video_at) }}</small
                    ><small class="table-subtitle" v-if="row.risk_failures"
                      >风险响应 {{ row.risk_failures }} 次</small
                    ></template
                  ></el-table-column
                ><el-table-column label="最近验证" min-width="180"
                  ><template #default="{ row }">{{
                    date(row.last_verified_at)
                  }}</template></el-table-column
                ><el-table-column label="操作" width="200"
                  ><template #default="{ row }"
                    ><el-button
                      size="small"
                      :loading="actionBusy === `/admin/accounts/${row.id}/verify`"
                      @click="action(`/admin/accounts/${row.id}/verify`, '验证账号')"
                      >验证账号</el-button
                    ><el-button size="small" @click="openEditor('accounts', row)"
                      >更新凭据</el-button
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
                    }}<small class="table-subtitle">{{ row.target_title || '归档任务' }}</small
                    ><small class="table-subtitle">{{ jobPhase(row) }}</small></template
                  ></el-table-column
                ><el-table-column label="状态" min-width="100"
                  ><template #default="{ row }"
                    ><span :class="`status-dot ${row.status}`">{{
                      jobStatusLabel(row) || statusText(row.status)
                    }}</span
                    ><small v-if="row.capture" class="table-subtitle">{{
                      mediaCaptureLabel(row)
                    }}</small></template
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
                    ><div class="admin-video-cell">
                      <img
                        v-if="row.cover_url"
                        :src="row.cover_url"
                        alt=""
                        loading="lazy"
                        width="104"
                        height="59"
                      />
                      <div>
                        <RouterLink :to="`/videos/${row.id}`">{{ row.title }}</RouterLink
                        ><small class="table-subtitle">{{ row.bvid }}</small>
                      </div>
                    </div></template
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
                ><el-table-column label="操作" width="156" fixed="right"
                  ><template #default="{ row }"
                    ><div class="content-row-actions">
                      <button class="row-action" @click="openEditor('videos', row)">
                        <UiIcon name="edit" />编辑
                      </button>
                      <button
                        v-if="isAdministrator"
                        class="row-action row-delete"
                        :aria-label="`删除视频：${row.title}`"
                        @click="deletionTarget = { kind: 'videos', id: row.id }"
                      >
                        <UiIcon name="trash" />删除
                      </button>
                    </div></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'creators'"
                ><el-table-column label="UP 主" min-width="220"
                  ><template #default="{ row }"
                    ><div class="admin-creator-cell">
                      <img
                        v-if="row.avatar_url"
                        :src="row.avatar_url"
                        alt=""
                        class="avatar"
                        loading="lazy"
                      /><span v-else class="avatar fallback">{{
                        row.name?.slice(0, 1) || '?'
                      }}</span>
                      <div>
                        <RouterLink :to="`/creators/${row.id}`">{{ row.name }}</RouterLink
                        ><small class="table-subtitle">UID {{ row.uid }}</small>
                      </div>
                    </div></template
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
                ><el-table-column label="操作" width="156" fixed="right"
                  ><template #default="{ row }"
                    ><div class="content-row-actions">
                      <button class="row-action" @click="openEditor('creators', row)">
                        <UiIcon name="edit" />编辑
                      </button>
                      <button
                        v-if="isAdministrator"
                        class="row-action row-delete"
                        :aria-label="`删除 UP 主：${row.name}`"
                        @click="deletionTarget = { kind: 'creators', id: row.id }"
                      >
                        <UiIcon name="trash" />删除
                      </button>
                    </div></template
                  ></el-table-column
                ></template
              >
              <template v-else-if="section === 'storage'"
                ><el-table-column prop="name" label="位置" min-width="160" /><el-table-column
                  label="类型"
                  width="130"
                  ><template #default="{ row }">{{
                    storageKindName(row.kind)
                  }}</template></el-table-column
                ><el-table-column label="状态" width="150"
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
                      @click="probeStorage(row)"
                      >测试连接</el-button
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
          <p v-if="feedback" class="action-feedback">
            <el-button @click="operationOpen = true">查看最近任务进度与结果</el-button>
          </p>
        </template>
      </main>
    </div>
    <OperationResult v-model:open="operationOpen" :job="feedback" @completed="load(page, true)" />
    <StorageProbeResult v-model:open="probeOpen" :result="probeResult" :name="probeName" />
    <DeletionDialog
      :target="deletionTarget"
      @close="deletionTarget = null"
      @started="showOperation"
    />
    <el-dialog
      v-model="dialog"
      :title="dialogTitle"
      :width="dialogKind === 'videos' ? 'min(760px, 94vw)' : 'min(680px, 94vw)'"
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
          ><IngestPolicyFields :value="form.policy"
        /></template>
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
          ><IngestPolicyFields :value="form.policy"
        /></template>
        <StorageFields v-if="dialogKind === 'storage'" :value="storageForm" />
        <template v-if="dialogKind === 'migrate'"
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
        <VideoEditorFields v-if="dialogKind === 'videos'" :value="form" />
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
<style scoped>
.admin-video-cell {
  display: flex;
  gap: 12px;
  align-items: center;
}
.admin-video-cell img {
  flex: 0 0 104px;
  width: 104px;
  height: 59px;
  object-fit: cover;
  border-radius: 6px;
}
.admin-video-cell > div {
  min-width: 0;
}
@media (max-width: 600px) {
  .admin-video-cell img {
    flex-basis: 72px;
    width: 72px;
    height: 41px;
  }
}
</style>
