<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { ElButton, ElDialog, ElSwitch, ElPagination } from 'element-plus';
import { api, write, date, errorText } from '../../api';
import type { Page, Row } from '../../types';
import { createScopedInterval } from '../../utils/scopedInterval';
import SourcePicker from './SourcePicker.vue';
import CommentBudgetFields from './CommentBudgetFields.vue';
import { commentBudgetDefaults } from '../../utils/commentBudget';

const route = useRoute();
const items = ref<Row[]>([]),
  accounts = ref<Row[]>([]),
  total = ref(0),
  page = ref(1);
const loading = ref(false),
  error = ref(''),
  notice = ref(''),
  saving = ref(false),
  open = ref(false);
const editId = ref(''),
  selected = ref<Row | null>(null),
  paidPrompt = ref<Row | null>(null),
  history = ref<Row[]>([]);
const form = reactive<Row>({});
const selectedSource = ref('');
const selectedSourceKey = ref('');
const startPolling = createScopedInterval(onBeforeUnmount);
let generation = 0;
const labels: Record<string, string> = {
  running: '正在检查',
  partial: '等待续查',
  complete: '全量检查完成',
  window_complete: '增量检查完成',
  unstable: '列表有变化，等待复查',
  blocked: '检查受阻',
  visible_traversal_complete: '全量检查完成',
};
const reasons: Record<string, string> = {
  verified_visible_end: '已核对当前可见列表',
  source_changed_during_scan: '检查期间来源列表发生变化，下一轮会重新校对',
  incremental_window: '已完成本轮增量窗口',
  page_budget: '已保存进度，等待继续检查',
  invalid_pagination: '来源分页异常，检查点已保留',
  pagination_loop: '来源返回重复页面，检查点已保留',
  risk_control: '来源触发风控，等待冷却',
};
const defaults = {
  ...commentBudgetDefaults,
  initial_strategy: 'all',
  initial_limit: 100,
  incremental_pages: 3,
  full_scan_interval_hours: 24,
  download_media: true,
  include_paid_videos: false,
  fetch_comments: true,
  fetch_danmaku: true,
  fetch_subtitles: true,
  quality: 'best',
  create_compatible_copy: false,
  prefer_dolby_vision: true,
  prefer_dolby_atmos: true,
  request_interval_seconds: 3,
  video_interval_seconds: 60,
  interval_jitter_seconds: 10,
  risk_cooldown_seconds: 900,
  fragment_concurrency: 1,
  request_budget: 100,
  max_pages: 100,
  max_download_bytes: 30_000_000_000,
  download_rate_bytes: null,
  include_auto_subtitles: true,
  prefer_h264: false,
};
const limits = [
  { key: 'risk_cooldown_seconds', label: '风控冷却（秒）', min: 60, max: 86400 },
  { key: 'fragment_concurrency', label: '下载分片并发', min: 1, max: 3 },
  { key: 'request_budget', label: '每轮请求预算', min: 1, max: 10000 },
  { key: 'max_pages', label: '每轮分页上限', min: 1, max: 10000 },
  {
    key: 'max_download_bytes',
    label: '单次下载大小上限（字节）',
    min: 1_000_000,
    max: 500_000_000_000,
  },
];

async function load(quiet = false) {
  const n = ++generation;
  if (!quiet) loading.value = true;
  try {
    const data = await api<Page<Row>>(`/admin/sources?page=${page.value}&page_size=20`);
    if (n === generation) {
      items.value = data.items;
      total.value = data.total;
      error.value = '';
    }
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) loading.value = false;
  }
}
async function edit(item?: Row) {
  error.value = '';
  notice.value = '';
  try {
    const settings = await api<Row>('/admin/settings');
    accounts.value = (await api<Page<Row>>('/admin/accounts?page_size=100')).items;
    Object.keys(form).forEach((key) => delete form[key]);
    selectedSource.value = '';
    selectedSourceKey.value = '';
    editId.value = item?.id || '';
    Object.assign(form, {
      kind: item?.kind || String(route.query.kind || 'favorite'),
      source_id: item?.source_id || String(route.query.source_id || ''),
      title: item?.title || String(route.query.title || ''),
      account_id: item?.account_id || accounts.value[0]?.id || '',
      interval_minutes: item?.interval_minutes || 60,
      enabled: item?.enabled ?? true,
      policy: {
        ...defaults,
        ...settings.ingest,
        ...item?.policy,
        include_paid_videos: item?.policy?.include_paid_videos === true,
      },
    });
    if (form.policy.quality === '8k') form.policy.quality = '4320p';
    if (form.policy.quality === '4k') form.policy.quality = '2160p';
    open.value = true;
  } catch (e) {
    error.value = errorText(e);
  }
}
async function resolve() {
  const parsed = await write('/admin/sources/resolve', {
    value: form.source_id.trim(),
    kind: form.kind,
  });
  form.source_id = parsed.source_id;
  form.kind = parsed.kind;
  if (
    form.title === selectedSource.value &&
    selectedSourceKey.value !== `${parsed.kind}:${parsed.source_id}`
  )
    form.title = '';
  if (!form.title.trim() && form.account_id) {
    const named = await api<Row>(
      `/admin/source-discovery/resolve?${new URLSearchParams({
        account_id: form.account_id,
        value: parsed.source_id,
        kind: parsed.kind,
      })}`,
    );
    form.title = named.title;
  }
}
function chooseSource(choice: { kind: string; source_id: string; title: string }) {
  form.kind = choice.kind;
  form.source_id = choice.source_id;
  if (!form.title.trim() || form.title === selectedSource.value) form.title = choice.title;
  selectedSource.value = choice.title;
  selectedSourceKey.value = `${choice.kind}:${choice.source_id}`;
}
async function save() {
  saving.value = true;
  error.value = '';
  try {
    if (!editId.value) await resolve();
    const body = {
      title: form.title.trim() || `${form.kind === 'creator' ? 'UP' : '收藏夹'} ${form.source_id}`,
      account_id: form.account_id,
      interval_minutes: form.interval_minutes,
      enabled: form.enabled,
      policy: form.policy,
      ...(!editId.value ? { kind: form.kind, source_id: form.source_id } : {}),
    };
    await write(
      `/admin/sources${editId.value ? '/' + editId.value : ''}`,
      body,
      editId.value ? 'PATCH' : 'POST',
    );
    open.value = false;
    notice.value = '备份来源已保存';
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function scan(item: Row, full = false) {
  error.value = '';
  notice.value = '';
  saving.value = true;
  try {
    await write(`/admin/sources/${item.id}/scan`, { full });
    notice.value = full ? '全量检查已加入队列' : '检查已加入队列';
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function allowPaid() {
  const item = paidPrompt.value;
  if (!item || saving.value) return;
  saving.value = true;
  error.value = '';
  try {
    await write(
      `/admin/sources/${item.id}`,
      { policy: { ...item.policy, include_paid_videos: true } },
      'PATCH',
    );
    await write(`/admin/sources/${item.id}/scan`, { full: true });
    paidPrompt.value = null;
    notice.value = '已允许保存充电视频，并加入完整检查队列';
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
async function toggle(item: Row, value: boolean | string | number) {
  try {
    await write(`/admin/sources/${item.id}`, { enabled: !!value }, 'PATCH');
    await load();
  } catch (e) {
    error.value = errorText(e);
  }
}
async function showHistory(item: Row) {
  error.value = '';
  try {
    history.value = (await api<Page<Row>>(`/admin/sources/${item.id}/history`)).items;
    selected.value = item;
  } catch (e) {
    error.value = errorText(e);
  }
}
onMounted(async () => {
  await load();
  if (route.query.source_id) {
    try {
      const matching = await api<Page<Row>>(
        `/admin/sources?kind=${encodeURIComponent(String(route.query.kind || 'favorite'))}&source_id=${encodeURIComponent(String(route.query.source_id))}&page_size=1`,
      );
      await edit(matching.items[0]);
    } catch (e) {
      error.value = errorText(e);
    }
  }
  startPolling(() => {
    if (!document.hidden && !open.value && !selected.value && !paidPrompt.value) void load(true);
  }, 30000);
});
onBeforeUnmount(() => {
  generation++;
});
</script>

<template>
  <section class="source-monitor">
    <div class="source-toolbar">
      <span>{{ total }} 个备份来源</span>
      <div>
        <el-button :loading="loading" @click="load()">刷新</el-button
        ><el-button type="primary" @click="edit()">添加来源</el-button>
      </div>
    </div>
    <p v-if="error && !open" class="form-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="source-notice" role="status">{{ notice }}</p>
    <div v-if="!items.length && !loading && !error" class="source-empty">
      <h2>自动保存你关注的视频</h2>
      <p>添加 UP 主或收藏夹，定期检查更新。</p>
      <el-button type="primary" @click="edit()">添加第一个来源</el-button>
    </div>
    <article v-for="item in items" :key="item.id" class="source-row">
      <div class="source-main">
        <span class="source-kind">{{ item.kind === 'creator' ? 'UP 主' : '收藏夹' }}</span>
        <h3>{{ item.title }}</h3>
        <el-switch
          :model-value="item.enabled"
          :aria-label="`${item.title} 自动检查`"
          @change="toggle(item, $event)"
        />
      </div>
      <div class="source-state">
        <span>{{ labels[item.monitor.status] || '尚未检查' }}</span
        ><span>上次完成 {{ date(item.monitor.last_completed_at) }}</span
        ><span>{{ item.enabled ? `下次检查 ${date(item.next_run_at)}` : '自动检查已暂停' }}</span>
      </div>
      <div v-if="item.monitor.counts" class="source-counts">
        <span
          >本轮发现 <b>{{ item.monitor.counts.new_items || 0 }}</b></span
        ><span
          >{{ item.kind === 'creator' ? '新投稿' : '新收藏' }}
          <b>{{
            item.kind === 'creator'
              ? item.monitor.counts.newly_published || 0
              : item.monitor.counts.newly_favorited || 0
          }}</b></span
        ><span
          >待归档 <b>{{ item.monitor.counts.queued || 0 }}</b></span
        ><span
          >已复用 <b>{{ item.monitor.counts.reused || 0 }}</b></span
        >
      </div>
      <div v-if="item.monitor.paid?.detected_count" class="source-paid-notice">
        <span
          >发现 {{ item.monitor.paid.detected_count }} 个充电视频<span
            v-if="item.monitor.paid.pending_count"
          >
            · {{ item.monitor.paid.pending_count }} 个等待确认</span
          ></span
        >
        <el-button
          v-if="item.monitor.paid.consent_required"
          :disabled="saving"
          @click="paidPrompt = item"
          >选择是否保存</el-button
        >
        <span v-else class="muted">{{
          item.policy?.include_paid_videos ? '已允许同步' : '已跳过'
        }}</span>
      </div>
      <div class="source-row-actions">
        <el-button :disabled="saving" @click="scan(item)">检查更新</el-button
        ><el-button :disabled="saving" @click="scan(item, true)">完整校对</el-button
        ><el-button @click="showHistory(item)">检查记录</el-button
        ><el-button @click="edit(item)">配置</el-button>
      </div>
    </article>
    <el-pagination
      v-if="total > 20"
      v-model:current-page="page"
      :total="total"
      :page-size="20"
      layout="prev, pager, next"
      @current-change="load()"
    />
    <el-dialog
      v-model="open"
      :title="editId ? '配置备份来源' : '添加备份来源'"
      width="640px"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
    >
      <form class="source-form" @submit.prevent="save">
        <div class="source-grid">
          <label
            >类型<select v-model="form.kind" :disabled="!!editId">
              <option value="favorite">收藏夹</option>
              <option value="creator">UP 主投稿</option>
            </select></label
          ><label
            >采集账号<select v-model="form.account_id" required>
              <option disabled value="">请选择</option>
              <option v-for="account in accounts" :key="account.id" :value="account.id">
                {{ account.name }}
              </option>
            </select></label
          >
        </div>
        <p v-if="!accounts.length" class="muted">
          请先在
          <RouterLink to="/admin/accounts" @click="open = false">B 站账号</RouterLink> 中添加授权。
        </p>
        <SourcePicker
          v-if="!editId && open"
          :account-id="form.account_id"
          :kind="form.kind"
          :source-id="form.source_id"
          @select="chooseSource"
        />
        <label
          >{{ form.kind === 'creator' ? '或手动填写 UP 主链接 / UID' : '或手动填写收藏夹链接 / ID'
          }}<input
            v-model="form.source_id"
            :disabled="!!editId"
            required
            placeholder="粘贴链接或填写数字 ID"
        /></label>
        <label
          >显示名称<input
            v-model="form.title"
            maxlength="500"
            placeholder="留空自动读取真实名称，也可填写自定义名称"
        /></label>
        <div class="source-grid">
          <label
            >检查间隔（分钟）<input
              v-model.number="form.interval_minutes"
              type="number"
              min="15"
              max="525600"
              required /></label
          ><label class="source-checkbox"
            ><input v-model="form.enabled" type="checkbox" /> 自动检查更新</label
          >
        </div>
        <fieldset>
          <legend>首次备份</legend>
          <div class="source-choice">
            <label
              ><input v-model="form.policy.initial_strategy" type="radio" value="all" />
              保存全部</label
            ><label
              ><input v-model="form.policy.initial_strategy" type="radio" value="latest" />
              只保存最近的</label
            ><label
              ><input v-model="form.policy.initial_strategy" type="radio" value="new_only" />
              从现在开始</label
            >
          </div>
          <label v-if="form.policy.initial_strategy === 'latest'"
            >最近条数<input
              v-model.number="form.policy.initial_limit"
              type="number"
              min="1"
              max="10000"
              required
          /></label>
          <p class="muted">首次检查建立完整清单。策略仅影响首次自动下载，之后按新增内容备份。</p>
        </fieldset>
        <fieldset>
          <legend>保存内容</legend>
          <div class="source-choice">
            <label><input v-model="form.policy.download_media" type="checkbox" /> 视频</label
            ><label><input v-model="form.policy.fetch_comments" type="checkbox" /> 评论</label
            ><label><input v-model="form.policy.fetch_danmaku" type="checkbox" /> 弹幕</label
            ><label><input v-model="form.policy.fetch_subtitles" type="checkbox" /> 字幕</label>
          </div>
        </fieldset>
        <fieldset>
          <legend>充电专属内容</legend>
          <label class="source-checkbox"
            ><input v-model="form.policy.include_paid_videos" type="checkbox" />
            也保存这个来源的充电视频</label
          >
          <p class="muted">
            只保存当前账号有权观看的内容。未勾选时会先记录发现的视频，再由你确认。
          </p>
        </fieldset>
        <CommentBudgetFields :value="form.policy" />
        <details>
          <summary>画质与检查策略</summary>
          <div class="source-grid">
            <label
              >画质<select v-model="form.policy.quality">
                <option value="best">最高可用画质</option>
                <option value="4320p">最高 8K</option>
                <option value="2160p">最高 4K</option>
                <option value="1440p">最高 1440P</option>
                <option value="1080p">最高 1080P</option>
                <option value="720p">最高 720P</option>
                <option value="480p">最高 480P</option>
                <option value="360p">最高 360P</option>
              </select></label
            ><label
              >全量校对间隔（小时）<input
                v-model.number="form.policy.full_scan_interval_hours"
                type="number"
                min="1"
                max="720"
                required /></label
            ><label
              >每次增量检查页数<input
                v-model.number="form.policy.incremental_pages"
                type="number"
                min="1"
                max="100"
                required /></label
            ><label
              >视频间隔（秒）<input
                v-model.number="form.policy.video_interval_seconds"
                type="number"
                min="10"
                max="3600"
                step="0.1"
                required /></label
            ><label
              >随机延迟上限（秒）<input
                v-model.number="form.policy.interval_jitter_seconds"
                type="number"
                min="0"
                max="300"
                step="0.1"
                required
            /></label>
          </div>
          <div class="source-choice">
            <label
              ><input v-model="form.policy.prefer_dolby_vision" type="checkbox" />
              优先杜比视界</label
            ><label
              ><input v-model="form.policy.prefer_dolby_atmos" type="checkbox" />
              优先杜比全景声</label
            ><label
              ><input v-model="form.policy.create_compatible_copy" type="checkbox" />
              另存兼容副本</label
            >
            <label
              ><input v-model="form.policy.prefer_h264" type="checkbox" />优先 H.264 编码</label
            >
            <label
              ><input
                v-model="form.policy.include_auto_subtitles"
                type="checkbox"
              />包含自动生成字幕</label
            >
          </div>
        </details>
        <details>
          <summary>采集限制</summary>
          <div class="source-grid">
            <label v-for="limit in limits" :key="limit.key"
              >{{ limit.label }}
              <input
                v-model.number="form.policy[limit.key]"
                type="number"
                :min="limit.min"
                :max="limit.max"
                required
              />
            </label>
            <label
              >下载限速（字节 / 秒）
              <input
                :value="form.policy.download_rate_bytes ?? ''"
                type="number"
                min="10000"
                max="1000000000"
                placeholder="不限制"
                @input="
                  form.policy.download_rate_bytes =
                    ($event.target as HTMLInputElement).value === ''
                      ? null
                      : Number(($event.target as HTMLInputElement).value)
                "
              />
            </label>
          </div>
          <p class="muted">预算用完会保存进度继续检查。并发越高不代表越稳定；长视频建议保持 1。</p>
        </details>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="source-form-actions">
          <el-button :disabled="saving" @click="open = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="saving"
            :disabled="!accounts.length"
            >保存</el-button
          >
        </div>
      </form>
    </el-dialog>
    <el-dialog
      :model-value="!!paidPrompt"
      title="保存充电视频？"
      width="480px"
      :close-on-click-modal="!saving"
      :close-on-press-escape="!saving"
      @close="paidPrompt = null"
    >
      <p>
        “{{ paidPrompt?.title }}”中发现
        {{ paidPrompt?.monitor.paid?.detected_count || 0 }} 个充电视频。
      </p>
      <p>允许后会保存这个来源中账号有权访问的充电视频，并在后续检查时同步新增内容。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <template #footer
        ><el-button :disabled="saving" @click="paidPrompt = null">暂不保存</el-button
        ><el-button type="primary" :loading="saving" @click="allowPaid"
          >保存全部可访问的充电视频</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      :model-value="!!selected"
      :title="`${selected?.title || ''} · 检查记录`"
      width="720px"
      @close="selected = null"
      ><p v-if="!history.length" class="muted">还没有检查记录。</p>
      <div v-for="run in history" :key="run.id" class="source-history">
        <strong>{{ date(run.started_at) }}</strong
        ><span>{{ labels[run.status] || run.status }}</span>
        <p>
          发现 {{ run.counts?.new_items || 0 }} · 入队 {{ run.counts?.queued || 0 }} · 观察
          {{ run.counts?.observed || 0 }} 项
        </p>
        <p v-if="run.end_reason" class="muted">
          {{ reasons[run.end_reason] || '详细原因可在任务中心查看' }}
        </p>
      </div></el-dialog
    >
  </section>
</template>

<style scoped>
.source-toolbar,
.source-main,
.source-state,
.source-counts,
.source-row-actions,
.source-form-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.source-paid-notice {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0 0;
  font-size: 13px;
  color: #826233;
  flex-wrap: wrap;
}
.source-toolbar {
  justify-content: space-between;
  margin-bottom: 20px;
}
.source-toolbar > span,
.source-state,
.source-counts {
  color: #7c828c;
  font-size: 12px;
}
.source-row {
  border-bottom: 1px solid #e7e9ed;
  padding: 20px 0;
}
.source-main h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
}
.source-kind {
  color: #1aa5c3;
  background: #eff9fc;
  padding: 3px 7px;
  font-size: 12px;
  border-radius: 4px;
}
.source-main .el-switch {
  margin-left: auto;
}
.source-state,
.source-counts {
  margin-top: 12px;
}
.source-counts b {
  color: #30353e;
  font-weight: 500;
}
.source-row-actions {
  margin-top: 16px;
  gap: 4px;
}
.source-empty {
  text-align: center;
  padding: 64px 16px;
}
.source-empty h2 {
  font-size: 20px;
  font-weight: 500;
}
.source-empty p {
  color: #8a9099;
  margin: 12px 0 24px;
}
.source-notice {
  color: #2a835b;
}
.source-form {
  display: grid;
  gap: 18px;
}
.source-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.source-form label {
  display: grid;
  gap: 8px;
  font-size: 13px;
}
.source-form input:not([type='checkbox']):not([type='radio']),
.source-form select {
  width: 100%;
  box-sizing: border-box;
}
.source-form fieldset {
  border: 1px solid #e8eaef;
  border-radius: 6px;
  padding: 14px;
}
.source-form legend {
  padding: 0 5px;
}
.source-choice {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.source-choice label,
.source-form .source-checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
}
.source-choice input,
.source-checkbox input {
  width: auto;
  accent-color: #16a0bd;
}
.source-checkbox {
  align-self: end;
  padding-bottom: 8px;
}
.source-form .muted {
  font-size: 12px;
  line-height: 1.6;
}
.source-form details .source-grid {
  margin: 16px 0;
}
.source-form-actions {
  justify-content: flex-end;
}
.source-history {
  border-bottom: 1px solid #eee;
  padding: 14px 0;
}
.source-history > span {
  float: right;
}
.source-history p {
  font-size: 13px;
}
.el-pagination {
  margin-top: 24px;
}
@media (max-width: 560px) {
  .source-grid {
    grid-template-columns: 1fr;
  }
  .source-main {
    gap: 8px;
  }
  .source-main h3 {
    max-width: 65%;
  }
  .source-state {
    gap: 7px;
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
