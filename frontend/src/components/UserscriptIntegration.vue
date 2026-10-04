<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { ElDialog, ElMessage, ElMessageBox } from 'element-plus';
import { api, date, errorText, statusText } from '../api';
import type { Page } from '../types';
import {
  newUserscriptTokenForm,
  userscriptFlags,
  userscriptPolicySummary,
  userscriptQualities,
  userscriptTokenPayload,
  userscriptTokenState,
  usableUserscriptAccount,
  type UserscriptToken,
} from '../utils/userscriptIntegration';

interface SourceAccount {
  id: string;
  name: string;
  status: string;
}
const endpoint = '/admin/integrations/userscript-tokens';
const items = ref<UserscriptToken[]>([]),
  accounts = ref<SourceAccount[]>([]);
const total = ref(0),
  tokenPage = ref(0),
  accountTotal = ref(0),
  accountPage = ref(0);
const loading = ref(false),
  accountsLoading = ref(false),
  creating = ref(false),
  revoking = ref('');
const listError = ref(''),
  accountError = ref(''),
  createError = ref(''),
  copyError = ref('');
const createOpen = ref(false),
  secretOpen = ref(false);
const issued = ref<{ item: UserscriptToken; token: string } | null>(null);
const revokedHere = ref(new Set<string>());
const form = reactive(newUserscriptTokenForm());
const origin = window.location.origin;
const addressInput = ref<HTMLInputElement>(),
  tokenInput = ref<HTMLTextAreaElement>();
const clock = ref(Date.now());
const mutating = computed(() => creating.value || !!revoking.value);
const usableAccounts = computed(() => accounts.value.filter(usableUserscriptAccount));
let disposed = false,
  listGeneration = 0,
  accountGeneration = 0;
let listRequest: AbortController | undefined, accountRequest: AbortController | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;

async function loadTokens(reset = true) {
  if (!reset && loading.value) return;
  const sequence = ++listGeneration,
    page = reset ? 1 : tokenPage.value + 1;
  listRequest?.abort();
  const controller = (listRequest = new AbortController());
  loading.value = true;
  listError.value = '';
  try {
    const result = await api<Page<UserscriptToken>>(`${endpoint}?page=${page}&page_size=50`, {
      signal: controller.signal,
    });
    if (disposed || sequence !== listGeneration) return;
    const rows = reset ? [] : items.value;
    items.value = [...new Map([...rows, ...result.items].map((item) => [item.id, item])).values()];
    total.value = result.total;
    tokenPage.value = page;
    clock.value = Date.now();
  } catch (error) {
    if (!disposed && sequence === listGeneration && !controller.signal.aborted)
      listError.value = errorText(error);
  } finally {
    if (!disposed && sequence === listGeneration) loading.value = false;
  }
}

async function loadAccounts(reset = true) {
  if (!reset && accountsLoading.value) return;
  const sequence = ++accountGeneration,
    page = reset ? 1 : accountPage.value + 1;
  accountRequest?.abort();
  const controller = (accountRequest = new AbortController());
  accountsLoading.value = true;
  accountError.value = '';
  try {
    const result = await api<Page<SourceAccount>>(`/admin/accounts?page=${page}&page_size=100`, {
      signal: controller.signal,
    });
    if (disposed || sequence !== accountGeneration) return;
    const rows = reset ? [] : accounts.value;
    accounts.value = [
      ...new Map([...rows, ...result.items].map((item) => [item.id, item])).values(),
    ];
    accountTotal.value = result.total;
    accountPage.value = page;
  } catch (error) {
    if (!disposed && sequence === accountGeneration && !controller.signal.aborted)
      accountError.value = errorText(error);
  } finally {
    if (!disposed && sequence === accountGeneration) accountsLoading.value = false;
  }
}

function beginCreate() {
  Object.assign(
    form,
    newUserscriptTokenForm(usableAccounts.value.length === 1 ? usableAccounts.value[0]!.id : ''),
  );
  createError.value = '';
  createOpen.value = true;
}
async function createToken() {
  if (creating.value) return;
  createError.value = '';
  let body;
  try {
    body = userscriptTokenPayload(
      form,
      usableAccounts.value.map((account) => account.id),
    );
  } catch (error) {
    createError.value = errorText(error);
    return;
  }
  creating.value = true;
  try {
    const result = await api<{ item: UserscriptToken; token: string }>(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (disposed) return;
    if (!result.item || typeof result.token !== 'string' || !result.token) {
      createError.value = '未收到完整令牌。请刷新列表，撤销刚创建的令牌后再试。';
      void loadTokens();
      return;
    }
    issued.value = result;
    copyError.value = '';
    createOpen.value = false;
    secretOpen.value = true;
    void loadTokens();
  } catch (error) {
    if (!disposed)
      createError.value = `${errorText(error)}。若网络中断，请先刷新列表确认是否已创建。`;
  } finally {
    if (!disposed) creating.value = false;
  }
}
function closeCreate(done: () => void) {
  if (!creating.value) done();
}
function forgetToken() {
  issued.value = null;
  copyError.value = '';
}
async function copy(value: string, secret = false) {
  copyError.value = '';
  try {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(value);
    if (!disposed) ElMessage.success(secret ? '令牌已复制' : '服务地址已复制');
  } catch {
    if (disposed) return;
    const input = secret ? tokenInput.value : addressInput.value;
    input?.focus();
    input?.select();
    const message = '浏览器未允许自动复制，内容已选中，请手动复制。';
    if (secret) copyError.value = message;
    else ElMessage.info(message);
  }
}
function tokenState(item: UserscriptToken) {
  return revokedHere.value.has(item.id)
    ? { key: 'revoked', label: '已撤销', usable: false }
    : userscriptTokenState(item, clock.value);
}
function accountName(item: UserscriptToken) {
  return (
    item.account_name ||
    accounts.value.find((account) => account.id === item.account_id)?.name ||
    '来源账号待确认'
  );
}
async function revoke(item: UserscriptToken) {
  if (mutating.value || tokenState(item).key === 'revoked') return;
  try {
    await ElMessageBox.confirm(
      `撤销“${item.name}”后，使用它的浏览器将无法继续提交采集。已有任务和归档不受影响。`,
      '撤销采集令牌',
      {
        confirmButtonText: '撤销令牌',
        cancelButtonText: '保留',
        type: 'warning',
      },
    );
  } catch {
    return;
  }
  if (disposed || mutating.value) return;
  revoking.value = item.id;
  try {
    await api(`${endpoint}/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
    if (disposed) return;
    revokedHere.value = new Set([...revokedHere.value, item.id]);
    ElMessage.success('令牌已撤销');
    await loadTokens();
  } catch (error) {
    if (!disposed) ElMessage.error(errorText(error));
  } finally {
    if (!disposed) revoking.value = '';
  }
}

onMounted(() => {
  void loadTokens();
  void loadAccounts();
  clockTimer = setInterval(() => {
    clock.value = Date.now();
  }, 60_000);
});
onBeforeUnmount(() => {
  disposed = true;
  listGeneration++;
  accountGeneration++;
  listRequest?.abort();
  accountRequest?.abort();
  if (clockTimer) clearInterval(clockTimer);
  issued.value = null;
});
</script>

<template>
  <section class="userscript-integration" aria-labelledby="userscript-title">
    <header class="integration-header">
      <div>
        <h2 id="userscript-title">安装与连接</h2>
      </div>
    </header>
    <div class="integration-setup">
      <article class="integration-step">
        <span class="step-number">1</span>
        <div>
          <h3>安装选片助手</h3>
          <p>需要 Tampermonkey 5.0 或更新版本。安装后，再打开下面的脚本链接。</p>
          <a
            class="integration-install"
            href="/userscripts/treasure-up.user.js"
            target="_blank"
            rel="noopener noreferrer"
            >安装选片助手 <span aria-hidden="true">↗</span></a
          >
        </div>
      </article>
      <article class="integration-step">
        <span class="step-number">2</span>
        <div>
          <h3>连接自己的视频库</h3>
          <p>将服务地址和新建的采集令牌填入脚本设置。</p>
          <div class="integration-address">
            <input
              ref="addressInput"
              :value="origin"
              readonly
              aria-label="本后台服务地址"
              autocomplete="off"
              spellcheck="false"
            />
            <button type="button" @click="copy(origin)">复制地址</button>
          </div>
        </div>
      </article>
    </div>
    <section class="integration-tokens" aria-labelledby="integration-tokens-title">
      <div class="token-heading">
        <div>
          <h3 id="integration-tokens-title">
            专用采集令牌 <span>{{ total }}</span>
          </h3>
          <p>每个浏览器可单独创建和撤销，无需填写后台密码或 B 站 Cookie。</p>
        </div>
        <div class="token-actions">
          <button type="button" :disabled="loading || mutating" @click="loadTokens()">刷新</button>
          <button
            type="button"
            class="integration-primary"
            :disabled="mutating || !!issued"
            @click="beginCreate"
          >
            新建令牌
          </button>
        </div>
      </div>
      <p v-if="listError" class="integration-error" role="alert">
        {{ listError }} <button type="button" @click="loadTokens()">重试</button>
      </p>
      <div v-if="loading && !items.length" class="integration-empty" role="status">
        正在读取令牌…
      </div>
      <div v-else-if="!items.length && !listError" class="integration-empty">
        <strong>还没有采集令牌</strong><span>创建一个令牌，让浏览器只获得提交采集的权限。</span>
      </div>
      <ul v-else class="token-list" :aria-busy="loading">
        <li v-for="item in items" :key="item.id" class="token-card">
          <div class="token-card-main">
            <div class="token-name">
              <strong>{{ item.name }}</strong
              ><span class="token-status" :class="tokenState(item).key">{{
                tokenState(item).label
              }}</span>
            </div>
            <p class="token-account">
              {{ accountName(item) }}
              <span>· {{ item.scope === 'ingest.submit' ? '仅提交采集' : '权限待确认' }}</span>
            </p>
            <p class="token-policy">{{ userscriptPolicySummary(item.policy) }}</p>
          </div>
          <dl class="token-times">
            <div>
              <dt>有效至</dt>
              <dd>{{ date(item.expires_at) }}</dd>
            </div>
            <div>
              <dt>最近使用</dt>
              <dd>{{ item.last_used_at ? date(item.last_used_at) : '尚未使用' }}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{{ date(item.created_at) }}</dd>
            </div>
          </dl>
          <button
            v-if="tokenState(item).key !== 'revoked'"
            type="button"
            class="token-revoke"
            :disabled="mutating"
            :aria-label="`撤销令牌 ${item.name}`"
            @click="revoke(item)"
          >
            {{ revoking === item.id ? '正在撤销…' : '撤销' }}
          </button>
          <span v-else class="token-revoked-label">已停止使用</span>
        </li>
      </ul>
      <div v-if="items.length < total" class="token-more">
        <button type="button" :disabled="loading || mutating" @click="loadTokens(false)">
          {{ loading ? '正在加载…' : '加载更多令牌' }}
        </button>
      </div>
    </section>

    <el-dialog
      v-model="createOpen"
      title="新建采集令牌"
      width="560px"
      class="integration-dialog"
      :close-on-click-modal="false"
      :before-close="closeCreate"
      destroy-on-close
    >
      <form class="token-form" autocomplete="off" @submit.prevent="createToken">
        <p class="token-form-intro">为这个浏览器取个名字，并选择后台已有的来源账号。</p>
        <label
          >令牌名称<input
            v-model="form.name"
            required
            maxlength="100"
            placeholder="例如：家用电脑 · Chrome"
            :disabled="creating"
        /></label>
        <label
          >来源账号<select
            v-model="form.account_id"
            required
            :disabled="creating || accountsLoading"
          >
            <option value="" disabled>
              {{ accountsLoading ? '正在读取账号…' : '请选择来源账号' }}
            </option>
            <option
              v-for="account in accounts"
              :key="account.id"
              :value="account.id"
              :disabled="!usableUserscriptAccount(account)"
            >
              {{ account.name }} · {{ statusText(account.status) }}
            </option>
          </select></label
        >
        <p v-if="accountError" class="integration-error" role="alert">
          {{ accountError }} <button type="button" @click="loadAccounts()">重新读取账号</button>
        </p>
        <p v-else-if="!accountsLoading && !usableAccounts.length" class="token-form-help">
          {{
            accounts.length
              ? '当前来源账号不可用，请先到“B 站账号”中验证或更新。'
              : '还没有来源账号，请先到“B 站账号”中添加。'
          }}
        </p>
        <button
          v-if="accounts.length < accountTotal"
          type="button"
          class="token-load-accounts"
          :disabled="accountsLoading || creating"
          @click="loadAccounts(false)"
        >
          加载更多来源账号
        </button>
        <div class="token-form-grid">
          <label
            >有效期<span class="days-input"
              ><input
                v-model="form.expires_days"
                type="number"
                min="1"
                max="365"
                step="1"
                required
                :disabled="creating"
              /><span>天</span></span
            ></label
          >
          <label
            >画质上限<select v-model="form.quality" :disabled="creating">
              <option v-for="quality in userscriptQualities" :key="quality" :value="quality">
                {{ quality === 'best' ? '最高可用画质' : quality.toUpperCase() }}
              </option>
            </select></label
          >
        </div>
        <fieldset :disabled="creating">
          <legend>保存内容</legend>
          <div class="token-policy-options">
            <label v-for="[key, label] in userscriptFlags" :key="key"
              ><input v-model="form[key]" type="checkbox" />{{ label }}</label
            >
          </div>
        </fieldset>
        <p class="token-form-help">
          实际画质取决于来源账号和视频。其余选项使用创建时的系统设置，之后更改系统设置不会改变此令牌。
        </p>
        <p v-if="createError" class="integration-error" role="alert">{{ createError }}</p>
        <footer class="token-form-footer">
          <button type="button" :disabled="creating" @click="createOpen = false">取消</button
          ><button
            type="submit"
            class="integration-primary"
            :disabled="creating || !usableAccounts.length || accountsLoading"
          >
            {{ creating ? '正在创建…' : '创建令牌' }}
          </button>
        </footer>
      </form>
    </el-dialog>
    <el-dialog
      v-model="secretOpen"
      title="保存你的采集令牌"
      width="580px"
      class="integration-dialog"
      :show-close="false"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="forgetToken"
    >
      <div v-if="issued" class="issued-token">
        <p class="issued-warning">
          完整令牌只显示这一次。关闭后无法再次查看，请复制到脚本设置中妥善保存。
        </p>
        <div class="issued-caption">
          <strong>{{ issued.item.name }}</strong
          ><span>有效至 {{ date(issued.item.expires_at) }}</span>
        </div>
        <textarea
          ref="tokenInput"
          :value="issued.token"
          rows="3"
          readonly
          spellcheck="false"
          autocomplete="off"
          aria-label="一次性采集令牌"
        />
        <p class="token-form-help">
          它仅用于提交采集。不要把令牌贴到评论、聊天或截图中；泄露后请在此撤销。
        </p>
        <p v-if="copyError" class="integration-error" role="alert">{{ copyError }}</p>
        <footer class="token-form-footer">
          <button type="button" @click="secretOpen = false">我已保存，隐藏令牌</button
          ><button type="button" class="integration-primary" @click="copy(issued.token, true)">
            复制令牌
          </button>
        </footer>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.userscript-integration {
  --integration-accent: var(--accent, #16847a);
  color: #253432;
}
.integration-header {
  margin-bottom: 24px;
}
.integration-eyebrow {
  margin: 0 0 8px;
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--integration-accent);
}
.integration-header h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.integration-description {
  margin: 10px 0 0;
  color: #78827f;
  font-size: 13px;
  line-height: 1.7;
}
.integration-setup {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 30px;
}
.integration-step {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 22px;
  border: 1px solid #e5ece8;
  border-radius: 12px;
  background: #fafcfb;
}
.step-number {
  display: grid;
  place-items: center;
  flex: 0 0 28px;
  height: 28px;
  border-radius: 50%;
  color: var(--integration-accent);
  background: #eaf3ef;
  font-size: 12px;
  font-weight: 600;
}
.integration-step > div {
  min-width: 0;
  width: 100%;
}
.integration-step h3 {
  margin: 3px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.integration-step p {
  margin: 0 0 16px;
  color: #78827f;
  font-size: 12px;
  line-height: 1.8;
}
.integration-install {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: var(--integration-accent);
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
}
.integration-install:hover {
  text-decoration: underline;
}
.integration-install span {
  font-size: 16px;
}
.integration-address {
  display: flex;
  align-items: center;
  gap: 8px;
}
.integration-address input {
  min-width: 0;
  flex: 1;
  width: 0;
  padding: 8px 9px;
  border: 1px solid #e0e8e3;
  border-radius: 6px;
  font-size: 12px;
  background: #fff;
  color: #52615b;
}
.integration-address button {
  flex-shrink: 0;
  font-size: 12px;
  padding: 8px 10px;
}
.token-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}
.token-heading h3 {
  margin: 0 0 7px;
  font-size: 16px;
  font-weight: 600;
}
.token-heading h3 span {
  margin-left: 8px;
  color: #99a29d;
  font-weight: 400;
  font-size: 13px;
}
.token-heading p {
  margin: 0;
  color: #7e8882;
  font-size: 12px;
  line-height: 1.7;
}
.token-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}
.userscript-integration button,
.token-form button,
.issued-token button {
  border: 1px solid #dfe7e2;
  border-radius: 7px;
  padding: 8px 13px;
  color: #53645b;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
}
.userscript-integration button:hover,
.token-form button:hover,
.issued-token button:hover {
  border-color: #b2c9bc;
  color: #16847a;
}
.userscript-integration button:disabled,
.token-form button:disabled {
  cursor: default;
  opacity: 0.5;
}
.userscript-integration .integration-primary,
.token-form .integration-primary,
.issued-token .integration-primary {
  background: #16847a;
  border-color: #16847a;
  color: #fff;
}
.integration-primary:hover {
  filter: brightness(0.95);
}
.integration-empty {
  display: grid;
  gap: 8px;
  place-content: center;
  text-align: center;
  min-height: 180px;
  border: 1px dashed #dfe7e2;
  border-radius: 10px;
  color: #929c96;
  font-size: 12px;
}
.integration-empty strong {
  font-size: 14px;
  color: #68786e;
  font-weight: 500;
}
.token-list {
  display: grid;
  gap: 12px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.token-card {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(240px, auto) 80px;
  align-items: center;
  gap: 22px;
  padding: 20px;
  border: 1px solid #e8ede9;
  border-radius: 10px;
  background: #fff;
}
.token-card-main {
  min-width: 0;
}
.token-name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
}
.token-name strong {
  font-size: 14px;
  font-weight: 550;
  overflow-wrap: anywhere;
}
.token-status {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 4px;
  background: #f0f2f1;
  color: #8a928d;
}
.token-status.active {
  color: #16847a;
  background: #eaf6f1;
}
.token-status.expired,
.token-status.unknown {
  color: #9a7635;
  background: #faf4e8;
}
.token-account {
  margin: 8px 0 6px;
  color: #68776d;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.token-account span {
  color: #909a93;
}
.token-policy {
  margin: 0;
  font-size: 11px;
  color: #929b96;
  line-height: 1.8;
}
.token-times {
  display: grid;
  gap: 6px;
  margin: 0;
  font-size: 11px;
  color: #7f8b83;
}
.token-times > div {
  display: flex;
  gap: 14px;
}
.token-times dt {
  min-width: 48px;
  color: #9aa39d;
}
.token-times dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}
.token-card .token-revoke {
  border: 0;
  background: transparent;
  color: #b77e73;
  padding: 8px;
}
.token-revoked-label {
  text-align: center;
  color: #a2aaa5;
  font-size: 11px;
}
.token-more {
  margin-top: 18px;
  text-align: center;
}
.integration-error {
  color: #b35746;
  font-size: 12px;
  line-height: 1.7;
  margin: 12px 0;
}
.integration-error button {
  margin-left: 8px;
}
.token-form,
.issued-token {
  color: #3b4d42;
}
.token-form-intro {
  margin: 0 0 20px;
  font-size: 12px;
  color: #8a948d;
  line-height: 1.7;
}
.token-form > label,
.token-form-grid > label {
  display: grid;
  gap: 7px;
  margin-bottom: 17px;
  font-size: 12px;
}
.token-form input:not([type='checkbox']),
.token-form select {
  box-sizing: border-box;
  width: 100%;
  padding: 10px 11px;
  border: 1px solid #dfe7e2;
  border-radius: 7px;
  background: #fff;
  color: #3b4d42;
  font: inherit;
  font-size: 13px;
}
.token-form input:focus,
.token-form select:focus,
.issued-token textarea:focus {
  outline: 2px solid rgb(22 132 122 / 14%);
  border-color: #16847a;
}
.token-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.days-input {
  position: relative;
}
.days-input input {
  padding-right: 38px !important;
}
.days-input > span {
  position: absolute;
  right: 12px;
  top: 12px;
  color: #929d95;
}
.token-form fieldset {
  border: 0;
  padding: 0;
  margin: 0 0 12px;
}
.token-form legend {
  font-size: 12px;
  margin-bottom: 11px;
}
.token-policy-options {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
}
.token-policy-options label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.token-policy-options input {
  accent-color: #16847a;
}
.token-form-help {
  margin: 9px 0 16px;
  color: #98a198;
  font-size: 11px;
  line-height: 1.8;
}
.token-form-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 10px;
}
.token-form .token-load-accounts {
  margin-bottom: 14px;
}
.issued-warning {
  padding: 12px 14px;
  border-radius: 8px;
  color: #8b6c37;
  background: #fbf6eb;
  margin: 0 0 18px;
  font-size: 12px;
  line-height: 1.8;
}
.issued-caption {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  margin-bottom: 9px;
}
.issued-caption strong {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.issued-caption span {
  font-size: 11px;
  color: #8d998f;
}
.issued-token textarea {
  box-sizing: border-box;
  width: 100%;
  resize: vertical;
  min-height: 90px;
  max-height: 200px;
  padding: 12px;
  border: 1px solid #dbe7df;
  border-radius: 8px;
  color: #355647;
  background: #f7faf8;
  font:
    12px/1.8 ui-monospace,
    SFMono-Regular,
    Consolas,
    monospace;
  overflow-wrap: anywhere;
}
@media (max-width: 1050px) {
  .integration-setup {
    grid-template-columns: 1fr;
  }
  .token-card {
    grid-template-columns: 1fr auto;
    gap: 14px;
  }
  .token-times {
    grid-column: 1;
    grid-row: 2;
  }
  .token-revoke,
  .token-revoked-label {
    grid-column: 2;
    grid-row: 1 / span 2;
  }
}
@media (max-width: 600px) {
  .integration-header h2 {
    font-size: 16px;
  }
  .integration-step {
    padding: 16px;
    gap: 10px;
  }
  .token-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .token-card {
    padding: 16px;
  }
  .token-name {
    align-items: flex-start;
  }
  .token-form-grid {
    gap: 10px;
  }
  .integration-address {
    flex-wrap: wrap;
  }
  .integration-address input {
    flex-basis: 100%;
    width: 100%;
  }
  .token-form-footer button {
    flex: 1;
  }
}
</style>
