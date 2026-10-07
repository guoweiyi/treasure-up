<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { ElButton, ElPagination, ElSwitch, vLoading } from 'element-plus';
import { api, date, errorText, session, sessionRevision, statusText } from '../../api';
import type { Page, Row } from '../../types';
import { refreshLabels, type SourceAccount } from '../../utils/accountAuthorization';
import AccountEditor from './AccountEditor.vue';
import OperationResult from './OperationResult.vue';

const items = ref<SourceAccount[]>([]),
  total = ref(0),
  page = ref(1);
const loading = ref(false),
  error = ref(''),
  notice = ref('');
const actionID = ref('');
const editor = ref<{ account?: SourceAccount; mode: 'qr' | 'manual' } | null>(null);
const job = ref<Row | null>(null),
  jobOpen = ref(false);
let generation = 0;
let request: AbortController | undefined;
let actionRequest: AbortController | undefined;
let disposed = false;

async function load(nextPage = page.value) {
  if (disposed || session.user?.role !== 'admin') return;
  const ticket = ++generation;
  request?.abort();
  request = new AbortController();
  loading.value = true;
  error.value = '';
  try {
    const data = await api<Page<SourceAccount>>(`/admin/accounts?page=${nextPage}&page_size=20`, {
      signal: request.signal,
    });
    if (ticket !== generation) return;
    items.value = data.items;
    total.value = data.total;
    page.value = nextPage;
  } catch (e) {
    if (ticket === generation) error.value = errorText(e);
  } finally {
    if (ticket === generation) loading.value = false;
  }
}
function edit(account?: SourceAccount, mode: 'qr' | 'manual' = 'qr') {
  if (actionID.value) return;
  notice.value = '';
  editor.value = { account, mode };
}
function saved(account: SourceAccount) {
  editor.value = null;
  notice.value = `已保存「${account.name}」的授权`;
  void load();
}
async function act(
  account: SourceAccount,
  kind: 'verify' | 'refresh' | 'toggle',
  enabled?: boolean,
) {
  if (actionID.value || disposed) return;
  const identity = sessionRevision.value;
  actionID.value = `${account.id}:${kind}`;
  actionRequest = new AbortController();
  error.value = '';
  notice.value = '';
  try {
    const result = await api<Row>(
      `/admin/accounts/${encodeURIComponent(account.id)}${kind === 'toggle' ? '' : `/${kind}`}`,
      {
        method: kind === 'toggle' ? 'PATCH' : 'POST',
        ...(kind === 'toggle' ? { body: JSON.stringify({ auto_refresh_enabled: enabled }) } : {}),
        signal: actionRequest.signal,
      },
    );
    if (disposed || identity !== sessionRevision.value) return;
    if (kind === 'toggle') notice.value = enabled ? '已开启自动续期' : '已关闭自动续期';
    else {
      job.value = result;
      jobOpen.value = true;
    }
    await load();
  } catch (e) {
    if (!disposed && identity === sessionRevision.value) error.value = errorText(e);
  } finally {
    if (!disposed && identity === sessionRevision.value) actionID.value = '';
  }
}
function reset() {
  generation++;
  request?.abort();
  actionRequest?.abort();
  items.value = [];
  total.value = 0;
  editor.value = null;
  jobOpen.value = false;
  job.value = null;
  error.value = '';
  notice.value = '';
  actionID.value = '';
  loading.value = false;
}
watch(
  sessionRevision,
  () => {
    reset();
    void load(1);
  },
  { flush: 'sync' },
);
onBeforeUnmount(() => {
  disposed = true;
  reset();
});
void load();
</script>

<template>
  <section class="account-manager">
    <div class="account-toolbar">
      <span>{{ total }} 个采集账号</span>
      <div>
        <el-button :loading="loading" @click="load()">刷新</el-button>
        <el-button :disabled="!!actionID" @click="edit(undefined, 'manual')">手动添加</el-button>
        <el-button type="primary" :disabled="!!actionID" @click="edit()">扫码添加账号</el-button>
      </div>
    </div>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="account-notice" role="status">{{ notice }}</p>
    <div v-loading="loading" class="account-list" :aria-busy="loading">
      <div v-if="!items.length && !loading && !error" class="account-empty">
        <h2>连接你的 B 站账号</h2>
        <p>扫码后在手机上确认，之后可自动检查和续期授权。</p>
        <el-button type="primary" @click="edit()">扫码添加第一个账号</el-button>
        <p class="account-empty-note">也可手动填写 Cookie，用于采集账号有权访问的内容。</p>
      </div>
      <article v-for="account in items" :key="account.id" class="account-row">
        <div class="account-identity">
          <h2>{{ account.name }}</h2>
          <span
            :class="[
              'account-status',
              { 'needs-attention': ['expired', 'invalid'].includes(account.status) },
            ]"
            >{{ statusText(account.status) }}</span
          >
          <span v-if="account.uid" class="account-uid">UID {{ account.uid }}</span>
        </div>
        <div class="account-refresh">
          <div class="account-refresh-info">
            <span
              :class="{
                'needs-attention': ['error', 'relogin_required'].includes(account.refresh_status),
              }"
              >{{ refreshLabels[account.refresh_status] || '尚未检查续期' }}</span
            >
            <span v-if="account.last_refreshed_at"
              >最近续期 {{ date(account.last_refreshed_at) }}</span
            >
            <span v-else-if="account.has_refresh_token">尚无续期记录</span>
            <span v-else>扫码授权或补入刷新令牌后可自动续期</span>
          </div>
          <label class="account-refresh-toggle"
            ><span>自动续期</span>
            <el-switch
              :model-value="account.auto_refresh_enabled && account.has_refresh_token"
              :disabled="!account.has_refresh_token || !!actionID"
              :loading="actionID === `${account.id}:toggle`"
              :aria-label="`${account.name} 自动续期`"
              @change="act(account, 'toggle', !!$event)"
          /></label>
        </div>
        <p
          v-if="account.refresh_message"
          :class="[
            'account-message',
            { 'needs-attention': ['error', 'relogin_required'].includes(account.refresh_status) },
          ]"
        >
          {{ account.refresh_message }}
        </p>
        <div class="account-bottom">
          <details class="account-details">
            <summary>授权详情</summary>
            <dl>
              <dt>刷新令牌</dt>
              <dd>{{ account.has_refresh_token ? '已配置' : '未配置' }}</dd>
              <dt>最近验证</dt>
              <dd>{{ date(account.last_verified_at || undefined) }}</dd>
              <dt>最近续期检查</dt>
              <dd>{{ date(account.last_refresh_check_at || undefined) }}</dd>
              <dt>下次续期检查</dt>
              <dd>
                {{
                  account.auto_refresh_enabled
                    ? date(account.next_refresh_at || undefined)
                    : '已关闭'
                }}
              </dd>
              <template v-if="account.cooldown_until"
                ><dt>采集冷却</dt>
                <dd>{{ date(account.cooldown_until) }}</dd></template
              >
              <template v-if="account.next_video_at"
                ><dt>下个视频</dt>
                <dd>{{ date(account.next_video_at) }}</dd></template
              >
              <template v-if="account.risk_failures"
                ><dt>风险响应</dt>
                <dd>{{ account.risk_failures }} 次</dd></template
              >
            </dl>
          </details>
          <div class="account-actions">
            <el-button text :disabled="!!actionID" @click="edit(account, 'manual')"
              >编辑凭据</el-button
            >
            <el-button text :disabled="!!actionID" @click="edit(account)">重新扫码</el-button>
            <el-button
              :disabled="!!actionID"
              :loading="actionID === `${account.id}:verify`"
              @click="act(account, 'verify')"
              >验证账号</el-button
            >
            <el-button
              :disabled="!account.has_refresh_token || !!actionID"
              :loading="actionID === `${account.id}:refresh`"
              @click="act(account, 'refresh')"
              >检查续期</el-button
            >
          </div>
        </div>
      </article>
    </div>
    <el-pagination
      v-if="total > 20"
      class="admin-pagination"
      :current-page="page"
      :page-size="20"
      :total="total"
      layout="prev, pager, next"
      @current-change="load"
    />
    <AccountEditor
      v-if="editor"
      :account="editor.account"
      :initial-mode="editor.mode"
      @close="editor = null"
      @saved="saved"
    />
    <OperationResult v-model:open="jobOpen" :job="job" @completed="load()" />
  </section>
</template>

<style scoped>
.account-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.account-toolbar > span {
  color: var(--muted);
  font-size: 12px;
}
.account-toolbar > div {
  display: flex;
  gap: 8px;
}
.account-toolbar .el-button + .el-button {
  margin: 0;
}
.account-list {
  min-height: 120px;
}
.account-row {
  padding: 22px 24px;
  border: 1px solid var(--border);
  background: #fff;
  margin-bottom: 14px;
  border-radius: 5px;
}
.account-identity {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.account-identity h2 {
  font-size: 16px;
  font-weight: 550;
  margin: 0;
  overflow-wrap: anywhere;
}
.account-status {
  font-size: 11px;
  color: var(--accent);
  background: #f2f5f3;
  border-radius: 3px;
  padding: 2px 6px;
}
.account-uid {
  font-size: 11px;
  color: #8b9095;
}
.account-refresh {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 17px;
}
.account-refresh-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  font-size: 12px;
}
.account-refresh-info > span:not(:first-child) {
  color: var(--muted);
}
.account-refresh-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
  color: var(--muted);
  font-size: 12px;
}
.account-message {
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0 0;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.needs-attention {
  color: #aa593a;
}
.account-bottom {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  padding-top: 13px;
  border-top: 1px solid #f0f1ef;
}
.account-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex-shrink: 0;
}
.account-actions .el-button + .el-button {
  margin: 0;
}
.account-details {
  font-size: 12px;
  color: var(--muted);
  padding-top: 8px;
}
.account-details summary {
  cursor: pointer;
}
.account-details dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px 16px;
  margin: 16px 0 6px;
  font-size: 11px;
}
.account-details dd {
  margin: 0;
  color: #50575c;
}
.account-notice {
  color: var(--accent);
  font-size: 13px;
}
.account-empty {
  padding: 48px 20px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: #fff;
  text-align: center;
}
.account-empty h2 {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 500;
}
.account-empty p {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 22px;
}
.account-empty .account-empty-note {
  font-size: 11px;
  margin: 17px 0 0;
}
@media (max-width: 850px) {
  .account-toolbar,
  .account-bottom {
    align-items: flex-start;
    flex-direction: column;
  }
  .account-row {
    padding: 18px;
  }
  .account-refresh {
    align-items: flex-start;
    gap: 12px;
  }
  .account-refresh-info {
    flex-direction: column;
    gap: 8px;
  }
  .account-actions {
    gap: 4px;
  }
}
</style>
