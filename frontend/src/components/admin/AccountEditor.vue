<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import {
  ElButton,
  ElCheckbox,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElSwitch,
} from 'element-plus';
import { api, date, errorText, sessionRevision } from '../../api';
import {
  accountDraft,
  accountPayload,
  authorizationActive,
  authorizationImage,
  authorizationState,
  createAccountAuthorization,
  type AuthorizationCreated,
  type AuthorizationResponse,
  type SourceAccount,
} from '../../utils/accountAuthorization';

const props = defineProps<{ account?: SourceAccount | null; initialMode: 'qr' | 'manual' }>();
const emit = defineEmits<{ close: []; saved: [account: SourceAccount] }>();
const mode = ref(props.initialMode);
const draft = reactive(accountDraft(props.account));
if (!props.account) draft.name = '我的 B 站账号';
const qr = reactive(authorizationState());
const saving = ref(false),
  error = ref('');
const identity = sessionRevision.value;
let alive = true;
let saveRequest: AbortController | undefined;
const existingTokenKept = computed(
  () => !!props.account?.has_refresh_token && !draft.cookie.trim() && !draft.removeRefreshToken,
);
const supportsRefresh = computed(
  () => !draft.removeRefreshToken && (!!draft.refreshToken.trim() || existingTokenKept.value),
);
const authorizing = computed(() => authorizationActive(qr.status));
const labels = {
  idle: '使用哔哩哔哩 App 扫码',
  starting: '正在生成二维码…',
  pending: '打开哔哩哔哩 App 扫一扫',
  scanned: '已扫码，请在手机上确认登录',
  validating: '已确认，正在保存授权…',
  success: '授权成功',
  expired: '二维码已过期',
  cancelled: '授权已取消',
  error: '暂时无法完成授权',
};
const authorization = createAccountAuthorization(qr, {
  create: (body, signal) =>
    api<AuthorizationCreated>('/admin/accounts/authorizations', {
      method: 'POST',
      body: JSON.stringify(body),
      signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
    }),
  poll: (id, signal) =>
    api<AuthorizationResponse>(`/admin/accounts/authorizations/${encodeURIComponent(id)}/poll`, {
      method: 'POST',
      signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
    }),
  cancel: (id) =>
    identity !== sessionRevision.value
      ? Promise.resolve()
      : api(`/admin/accounts/authorizations/${encodeURIComponent(id)}/cancel`, {
          method: 'POST',
          signal: AbortSignal.timeout(10000),
        }),
  render: authorizationImage,
  complete: (account) => {
    if (alive) emit('saved', account);
  },
});
function selectMode(value: 'qr' | 'manual') {
  if (saving.value || value === mode.value) return;
  authorization.stop();
  draft.cookie = '';
  draft.refreshToken = '';
  draft.removeRefreshToken = false;
  error.value = '';
  mode.value = value;
}
async function generate() {
  if (authorizing.value || saving.value) return;
  error.value = '';
  const name = draft.name.trim();
  if (!name || name.length > 200) {
    error.value = '请填写账号名称';
    return;
  }
  await authorization.start({ name, ...(props.account ? { account_id: props.account.id } : {}) });
}
async function save() {
  if (saving.value || authorizing.value || !alive) return;
  error.value = '';
  try {
    const payload = accountPayload(draft, !!props.account);
    saving.value = true;
    saveRequest = new AbortController();
    const result = await api<SourceAccount>(
      `/admin/accounts${props.account ? `/${encodeURIComponent(props.account.id)}` : ''}`,
      {
        method: props.account ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
        signal: saveRequest.signal,
      },
    );
    draft.cookie = '';
    draft.refreshToken = '';
    if (alive) emit('saved', result);
  } catch (e) {
    if (alive) error.value = errorText(e);
  } finally {
    if (alive) saving.value = false;
  }
}
function close() {
  if (!alive) return;
  alive = false;
  authorization.dispose();
  saveRequest?.abort();
  draft.cookie = '';
  draft.refreshToken = '';
  emit('close');
}
watch(sessionRevision, close, { flush: 'sync' });
onBeforeUnmount(() => {
  alive = false;
  authorization.dispose();
  saveRequest?.abort();
  draft.cookie = '';
  draft.refreshToken = '';
});
</script>

<template>
  <el-dialog
    :model-value="true"
    :title="account ? '更新账号授权' : '添加 B 站账号'"
    width="min(540px, 94vw)"
    :show-close="!saving"
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    @update:model-value="close"
  >
    <div class="account-methods" role="tablist" aria-label="账号授权方式">
      <button
        id="account-qr-tab"
        type="button"
        role="tab"
        :aria-selected="mode === 'qr'"
        aria-controls="account-qr-panel"
        :disabled="saving"
        @click="selectMode('qr')"
      >
        扫码授权
      </button>
      <button
        id="account-manual-tab"
        type="button"
        role="tab"
        :aria-selected="mode === 'manual'"
        aria-controls="account-manual-panel"
        :disabled="saving"
        @click="selectMode('manual')"
      >
        手动填写
      </button>
    </div>
    <el-form label-position="top" @submit.prevent="mode === 'manual' ? save() : generate()">
      <el-form-item label="账号名称" required>
        <el-input
          v-model="draft.name"
          maxlength="200"
          :disabled="authorizing || saving"
          autocomplete="off"
          placeholder="用于区分采集账号"
        />
      </el-form-item>
      <div
        v-if="mode === 'qr'"
        id="account-qr-panel"
        role="tabpanel"
        aria-labelledby="account-qr-tab"
        class="account-qr"
      >
        <p class="account-method-help">手机确认后即可保存 Cookie 与刷新令牌，用于后续自动续期。</p>
        <div class="account-qr-image" :aria-busy="qr.status === 'starting'">
          <img v-if="qr.image" :src="qr.image" alt="B 站账号授权二维码" width="220" height="220" />
          <div v-else class="account-qr-placeholder">
            <span aria-hidden="true">▦</span>
            <el-button
              :loading="qr.status === 'starting'"
              :disabled="authorizing"
              @click="generate"
            >
              {{ qr.status === 'idle' ? '生成二维码' : '重新生成二维码' }}
            </el-button>
          </div>
        </div>
        <p class="account-qr-status" role="status">{{ labels[qr.status] }}</p>
        <p v-if="qr.expiresAt && authorizing" class="account-qr-expiry">
          有效至 {{ date(qr.expiresAt) }}
        </p>
        <p v-if="account?.uid" class="account-method-help">
          请使用原账号（UID {{ account.uid }}）扫码。
        </p>
        <p v-if="qr.error" class="form-error" role="alert">{{ qr.error }}</p>
      </div>
      <div v-else id="account-manual-panel" role="tabpanel" aria-labelledby="account-manual-tab">
        <el-form-item label="Cookie" :required="!account">
          <el-input
            v-model="draft.cookie"
            type="password"
            show-password
            maxlength="65536"
            autocomplete="new-password"
            :disabled="saving"
            :placeholder="account ? '留空保留现有 Cookie' : '粘贴已登录 B 站的 Cookie'"
          />
        </el-form-item>
        <el-form-item label="刷新令牌（可选）">
          <el-input
            v-model="draft.refreshToken"
            type="password"
            show-password
            maxlength="4096"
            autocomplete="new-password"
            :disabled="saving || draft.removeRefreshToken"
            placeholder="refresh_token / ac_time_value"
          />
          <p class="account-method-help">
            {{
              existingTokenKept
                ? '已保存刷新令牌，留空保留。'
                : '只填写 Cookie 也能采集；添加刷新令牌后可自动续期。'
            }}
          </p>
          <p
            v-if="account?.has_refresh_token && draft.cookie.trim() && !draft.refreshToken.trim()"
            class="account-token-warning"
          >
            更换 Cookie 会移除旧刷新令牌，请同时填写与新 Cookie 配套的令牌。
          </p>
          <el-checkbox
            v-if="account?.has_refresh_token"
            v-model="draft.removeRefreshToken"
            :disabled="saving || !!draft.refreshToken.trim()"
            >移除已保存的刷新令牌</el-checkbox
          >
        </el-form-item>
        <div class="account-auto-refresh">
          <div>
            <strong>自动续期</strong>
            <p>定期检查，仅在 B 站要求时更新凭据。</p>
          </div>
          <el-switch
            :model-value="draft.autoRefresh && supportsRefresh"
            :disabled="saving || !supportsRefresh"
            aria-label="自动续期"
            @change="draft.autoRefresh = !!$event"
          />
        </div>
        <details class="account-manual-help">
          <summary>在哪里获取刷新令牌？</summary>
          <p>
            在你已登录的 B 站网页打开浏览器开发者工具，进入「应用 / Application → 本地存储 / Local
            Storage」，复制 ac_time_value 的值。它不在 Cookie 中，需与同一次登录的 Cookie 配套。
          </p>
          <p>若没有该字段，可改用扫码授权。已保存的凭据不会回显，表单内容不会写入浏览器存储。</p>
        </details>
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="close">{{
        authorizing ? '取消授权' : '关闭'
      }}</el-button>
      <el-button v-if="mode === 'manual'" type="primary" :loading="saving" @click="save"
        >保存账号</el-button
      >
    </template>
  </el-dialog>
</template>

<style scoped>
.account-methods {
  display: flex;
  gap: 24px;
  border-bottom: 1px solid var(--border);
  margin: -4px 0 22px;
}
.account-methods button {
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 10px 0;
  background: transparent;
}
.account-methods button[aria-selected='true'] {
  color: var(--accent);
  border-color: var(--accent);
}
.account-method-help,
.account-auto-refresh p,
.account-manual-help {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  margin: 8px 0 0;
}
.account-qr {
  text-align: center;
}
.account-qr-image {
  width: 242px;
  height: 242px;
  margin: 18px auto 12px;
  padding: 10px;
  background: #fff;
  border: 1px solid var(--border);
}
.account-qr-image img {
  display: block;
  object-fit: contain;
  max-width: 100%;
}
.account-qr-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  background: #f7f8f7;
}
.account-qr-placeholder > span {
  color: #8d9690;
  font-size: 42px;
}
.account-qr-status {
  font-size: 14px;
  margin: 12px 0 5px;
  color: var(--text);
}
.account-qr-expiry {
  color: var(--muted);
  font-size: 11px;
  margin: 0;
}
.account-token-warning {
  color: #9c6b27;
  font-size: 12px;
  line-height: 1.7;
  margin: 8px 0 0;
}
.account-auto-refresh {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  border-top: 1px solid var(--border);
  padding-top: 16px;
}
.account-auto-refresh strong {
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
}
.account-auto-refresh p {
  margin-top: 3px;
}
.account-manual-help {
  margin-top: 18px;
}
.account-manual-help summary {
  cursor: pointer;
}
.account-manual-help p {
  margin: 8px 0;
}
</style>
