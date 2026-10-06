<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api, write, session, date, errorText } from '../api';
import type { User } from '../types';

type IdentityToken = {
  id: string;
  name: string;
  created_at: string;
  expires_at: string;
  last_used_at?: string | null;
  current_session?: boolean;
};
const router = useRouter();
const errorKind = ref<'tokens' | 'password'>('tokens');
const items = ref<IdentityToken[]>([]),
  loading = ref(true),
  busy = ref(false),
  error = ref(''),
  message = ref('');
const adding = ref(false),
  name = ref(''),
  password = ref(''),
  expires = ref(30),
  removing = ref('');
const issued = ref(''),
  showToken = ref(false),
  tokenInput = ref<HTMLInputElement>();
const changing = ref(false),
  currentPassword = ref(''),
  newPassword = ref(''),
  confirmPassword = ref('');
const canChange = computed(
  () => newPassword.value.length >= 12 && newPassword.value === confirmPassword.value,
);
let alive = true;
async function load() {
  errorKind.value = 'tokens';
  loading.value = true;
  error.value = '';
  try {
    const data = await api<{ items: IdentityToken[] }>('/auth/identity-tokens');
    if (alive) items.value = data.items;
  } catch (e) {
    if (alive) error.value = errorText(e);
  } finally {
    if (alive) loading.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => {
  alive = false;
  issued.value =
    password.value =
    currentPassword.value =
    newPassword.value =
    confirmPassword.value =
      '';
});
async function create() {
  if (busy.value) return;
  errorKind.value = 'tokens';
  busy.value = true;
  error.value = message.value = '';
  try {
    const data = await write<{ item: IdentityToken; token: string }>('/auth/identity-tokens', {
      name: name.value.trim() || '个人设备',
      password: password.value,
      expires_days: expires.value,
    });
    if (!alive) return;
    issued.value = data.token;
    showToken.value = false;
    items.value.unshift(data.item);
    adding.value = false;
    name.value = '';
  } catch (e) {
    if (alive) error.value = errorText(e);
  } finally {
    password.value = '';
    busy.value = false;
  }
}
async function revoke(id: string) {
  if (busy.value) return;
  errorKind.value = 'tokens';
  busy.value = true;
  error.value = message.value = '';
  try {
    const current = items.value.find((item) => item.id === id)?.current_session;
    await write(`/auth/identity-tokens/${id}`, undefined, 'DELETE');
    if (!alive) return;
    if (current) {
      session.user = null;
      session.csrf = '';
      await router.replace({ path: '/login', query: { revoked: '1' } });
      return;
    }
    items.value = items.value.filter((item) => item.id !== id);
    removing.value = '';
    issued.value = '';
    message.value = '身份令牌已撤销。';
  } catch (e) {
    if (alive) error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
async function copyToken() {
  try {
    await navigator.clipboard.writeText(issued.value);
    message.value = '身份令牌已复制。';
  } catch {
    showToken.value = true;
    tokenInput.value?.focus();
    tokenInput.value?.select();
    message.value = '请选中令牌并手动复制。';
  }
}
async function changePassword() {
  if (busy.value || !canChange.value) return;
  errorKind.value = 'password';
  busy.value = true;
  error.value = message.value = '';
  try {
    const data = await write<{ user: User; csrf_token: string }>('/auth/password', {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });
    if (!alive) return;
    currentPassword.value = newPassword.value = confirmPassword.value = '';
    // Changing CSRF remounts account views; carry only the non-sensitive success state.
    await router.replace({ path: '/account/security', query: { password_updated: '1' } });
    session.user = data.user;
    session.csrf = data.csrf_token;
  } catch (e) {
    if (alive) error.value = errorText(e);
  } finally {
    currentPassword.value = '';
    busy.value = false;
  }
}
</script>
<template>
  <section class="credential-panel">
    <header class="credential-heading">
      <div>
        <h2>身份令牌</h2>
        <p>为自己的设备创建独立登录凭据，随时撤销。</p>
      </div>
      <button
        :disabled="busy || loading || adding"
        @click="
          adding = true;
          issued = '';
          error = '';
          message = '';
        "
      >
        创建令牌
      </button>
    </header>
    <div v-if="issued" class="issued-token" role="status">
      <strong>令牌仅在这次显示，请妥善保存</strong>
      <p>在登录页选择「身份令牌」后粘贴。它拥有你当前账户的权限。</p>
      <input
        ref="tokenInput"
        :type="showToken ? 'text' : 'password'"
        :value="issued"
        readonly
        autocomplete="off"
        aria-label="刚创建的身份令牌"
      />
      <div class="credential-actions">
        <button type="button" @click="copyToken">复制令牌</button
        ><button type="button" @click="showToken = !showToken">
          {{ showToken ? '隐藏' : '显示' }}</button
        ><button
          type="button"
          class="text-button"
          @click="
            issued = '';
            showToken = false;
          "
        >
          已保存，关闭
        </button>
      </div>
    </div>
    <form v-if="adding" class="credential-form" @submit.prevent="create">
      <div class="credential-columns">
        <label
          >用途<input
            v-model="name"
            maxlength="80"
            autocomplete="off"
            placeholder="如：家里的 iPad"
            required
            :disabled="busy" /></label
        ><label
          >有效期<select v-model.number="expires" :disabled="busy">
            <option :value="7">7 天</option>
            <option :value="30">30 天</option>
            <option :value="90">90 天</option>
          </select></label
        >
      </div>
      <label
        >当前密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
          maxlength="256"
          :disabled="busy"
      /></label>
      <div class="credential-actions">
        <button type="submit" class="primary" :disabled="busy">
          {{ busy ? '正在创建…' : '创建并显示令牌' }}</button
        ><button
          type="button"
          :disabled="busy"
          @click="
            adding = false;
            password = '';
          "
        >
          取消
        </button>
      </div>
    </form>
    <p v-if="loading" class="credential-empty">正在读取身份令牌…</p>
    <ul v-else-if="items.length" class="token-list">
      <li v-for="item in items" :key="item.id">
        <div>
          <strong
            >{{ item.name }}<small v-if="item.current_session">当前登录</small
            ><small v-if="Date.parse(item.expires_at) <= Date.now()">已过期</small></strong
          ><span
            >有效至 {{ date(item.expires_at) }} ·
            {{ item.last_used_at ? `上次使用 ${date(item.last_used_at)}` : '尚未使用' }}</span
          >
        </div>
        <div v-if="removing === item.id" class="credential-actions">
          <span v-if="item.current_session" class="revoke-current-note">此设备将退出登录</span>
          <button :disabled="busy" @click="revoke(item.id)">确认撤销</button
          ><button class="text-button" :disabled="busy" @click="removing = ''">取消</button>
        </div>
        <button v-else class="text-button" :disabled="busy" @click="removing = item.id">
          撤销
        </button>
      </li>
    </ul>
    <p v-else-if="!error && !adding && !issued" class="credential-empty">
      还没有身份令牌。账号密码与通行密钥仍可正常登录。
    </p>
    <p class="credential-help">
      身份令牌用于登录视频库；浏览器选片助手使用管理中心单独签发的采集令牌。
    </p>
    <p v-if="error && errorKind === 'tokens'" class="form-error" role="alert">
      {{ error }}
      <button v-if="!items.length && !busy" type="button" class="text-button" @click="load">
        重新读取
      </button>
    </p>
    <p v-if="message" class="credential-success" role="status">{{ message }}</p>
  </section>
  <section class="credential-panel">
    <header class="credential-heading">
      <div>
        <h2>登录密码</h2>
        <p>更新密码后，其他登录会话和身份令牌会同时失效。</p>
      </div>
      <button
        :disabled="busy"
        @click="
          changing = !changing;
          currentPassword = '';
          newPassword = '';
          confirmPassword = '';
        "
      >
        {{ changing ? '取消修改' : '修改密码' }}
      </button>
    </header>
    <form v-if="changing" class="credential-form" @submit.prevent="changePassword">
      <input
        :value="session.user?.username"
        autocomplete="username"
        class="credential-username"
        tabindex="-1"
        aria-hidden="true"
      />
      <label
        >当前密码<input
          v-model="currentPassword"
          type="password"
          autocomplete="current-password"
          required
          maxlength="256"
          :disabled="busy"
      /></label>
      <label
        >新密码<input
          v-model="newPassword"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="256"
          :disabled="busy"
          placeholder="至少 12 个字符"
      /></label>
      <label
        >再次输入新密码<input
          v-model="confirmPassword"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="256"
          :disabled="busy"
      /></label>
      <p v-if="confirmPassword && newPassword !== confirmPassword" class="form-error">
        两次新密码不一致。
      </p>
      <div class="credential-actions">
        <button class="primary" :disabled="busy || !canChange">
          {{ busy ? '正在保存…' : '保存新密码' }}
        </button>
      </div>
    </form>
    <p v-if="error && errorKind === 'password'" class="form-error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.credential-panel {
  margin-top: 20px;
  padding: 24px;
  border: 1px solid #e7e9ed;
  border-radius: 8px;
}
.credential-heading {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
}
.credential-heading h2 {
  margin: 0;
  font-size: 18px;
}
.credential-heading p,
.credential-help {
  color: #8b9099;
  font-size: 12px;
  line-height: 1.7;
}
.credential-heading p {
  margin: 9px 0 0;
}
.credential-heading > button {
  flex-shrink: 0;
}
.credential-empty {
  padding: 20px 0 6px;
  color: #777;
  font-size: 13px;
}
.credential-form {
  margin-top: 24px;
  padding-top: 4px;
  border-top: 1px solid #eee;
}
.credential-form label {
  display: grid;
  gap: 8px;
  margin-top: 16px;
  font-size: 13px;
}
.credential-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 130px;
  gap: 16px;
}
.credential-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
}
.credential-actions > button {
  font-size: 12px;
}
.token-list {
  list-style: none;
  margin: 20px 0;
  padding: 0;
}
.token-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  border-top: 1px solid #eee;
  padding: 18px 0;
}
.token-list strong {
  font-size: 14px;
  overflow-wrap: anywhere;
}
.token-list strong small {
  margin-left: 8px;
  color: #8b9099;
  font-size: 10px;
  font-weight: 400;
}
.token-list .revoke-current-note {
  width: 100%;
  margin-top: 0;
}
.token-list span {
  display: block;
  color: #8b9099;
  font-size: 12px;
  margin-top: 7px;
  line-height: 1.6;
}
.token-list .credential-actions {
  margin-top: 0;
  flex-shrink: 0;
}
.issued-token {
  padding: 18px;
  background: #f3f7f6;
  margin-top: 20px;
  border: 1px solid #dde9e3;
  border-radius: 6px;
}
.issued-token strong {
  font-size: 14px;
}
.issued-token p {
  color: #68756e;
  font-size: 12px;
  line-height: 1.7;
}
.issued-token input {
  width: 100%;
  font-family: monospace;
  font-size: 12px;
}
.credential-success {
  color: #278354;
  font-size: 13px;
}
.credential-username {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
@media (max-width: 520px) {
  .credential-panel {
    padding: 18px;
  }
  .credential-heading {
    flex-direction: column;
    gap: 14px;
  }
  .token-list li {
    flex-wrap: wrap;
  }
}
</style>
