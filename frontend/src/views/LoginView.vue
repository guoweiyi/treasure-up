<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  session,
  write,
  errorText,
  loadDisplaySettings,
  display,
  authStatus,
  loadAuthStatus,
} from '../api';
import type { User } from '../types';
import UiIcon from '../components/UiIcon.vue';
import { loginDestination } from '../utils/accessPolicy';
import {
  getPasskeyCapabilities,
  loginWithPasskey,
  passkeyError,
  supportsPasskeys,
} from '../auth/passkeys';
import type { PasskeyCapabilities } from '../auth/passkeys';

const username = ref(''),
  password = ref(''),
  token = ref('');
const busy = ref(false),
  error = ref(''),
  statusError = ref('');
const mode = ref<'password' | 'token'>('password');
const showSecret = ref(false),
  capsLock = ref(false);
const router = useRouter(),
  route = useRoute();
const capabilities = ref<PasskeyCapabilities | null>(null);
const passkeyAvailable = computed(() => supportsPasskeys() && capabilities.value?.available);
const initialized = computed(() => authStatus.initialized !== false);
async function checkStatus() {
  statusError.value = '';
  try {
    await loadAuthStatus();
  } catch {
    statusError.value = '暂时无法连接服务器，请检查连接后重试。';
  }
}
onMounted(async () => {
  await Promise.allSettled([
    checkStatus(),
    getPasskeyCapabilities().then((value) => {
      capabilities.value = value;
    }),
  ]);
});
function changeMode(value: 'password' | 'token') {
  mode.value = value;
  password.value = '';
  token.value = '';
  showSecret.value = false;
  error.value = '';
  capsLock.value = false;
}
async function acceptLogin(data: { user: User; csrf_token: string }) {
  password.value = '';
  token.value = '';
  session.user = data.user;
  session.csrf = data.csrf_token;
  session.ready = true;
  await loadDisplaySettings();
  await router.replace(loginDestination(route.query.next));
}
async function passkeyLogin() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    await acceptLogin(await loginWithPasskey());
  } catch (e) {
    error.value = passkeyError(e);
  } finally {
    busy.value = false;
  }
}
async function login() {
  if (busy.value || !initialized.value) return;
  if (mode.value === 'token' && token.value.trim().startsWith('tu_ingest_')) {
    error.value = '这是选片助手的采集令牌。请使用「账户安全」中创建的个人身份令牌。';
    return;
  }
  busy.value = true;
  error.value = '';
  try {
    const data =
      mode.value === 'token'
        ? await write('/auth/token-login', { token: token.value.trim() })
        : await write('/auth/login', { username: username.value.trim(), password: password.value });
    await acceptLogin(data);
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="login-page">
    <form class="login-card" :aria-busy="busy" @submit.prevent="login">
      <div class="brand">
        <span class="brand-mark"><UiIcon name="play" /></span><span>{{ display.site_name }}</span>
      </div>
      <h1>{{ initialized ? '登录视频库' : '准备你的视频库' }}</h1>
      <p class="login-intro">
        {{ initialized ? '继续观看，收藏与整理。' : '服务器已连接，等待完成首次初始化。' }}
      </p>
      <div v-if="!initialized" class="setup-note" role="status">
        <h2>完成首次启动</h2>
        <ol>
          <li>在部署目录运行 <code>python deploy/start.py</code>。</li>
          <li>等待数据库与服务就绪，终端会显示首次创建的管理员账号和密码。</li>
          <li>回到这里登录，然后在管理中心配置采集账号和存储。</li>
        </ol>
        <button type="button" @click="checkStatus">重新检查</button>
      </div>
      <template v-else>
        <div class="login-methods" role="group" aria-label="登录方式">
          <button
            type="button"
            :class="{ selected: mode === 'password' }"
            :aria-pressed="mode === 'password'"
            :disabled="busy"
            @click="changeMode('password')"
          >
            账号密码
          </button>
          <button
            type="button"
            :class="{ selected: mode === 'token' }"
            :aria-pressed="mode === 'token'"
            :disabled="busy"
            @click="changeMode('token')"
          >
            身份令牌
          </button>
        </div>
        <label v-if="mode === 'password'" class="login-field"
          >用户名
          <input
            v-model="username"
            name="username"
            autocomplete="username"
            maxlength="100"
            required
            :disabled="busy"
            autofocus
          />
        </label>
        <label class="login-field" :for="mode === 'password' ? 'login-password' : 'login-token'">{{
          mode === 'password' ? '密码' : '身份令牌'
        }}</label>
        <div class="secret-field">
          <input
            v-if="mode === 'password'"
            id="login-password"
            v-model="password"
            name="password"
            :type="showSecret ? 'text' : 'password'"
            autocomplete="current-password"
            maxlength="256"
            required
            :disabled="busy"
            @keydown="capsLock = $event.getModifierState('CapsLock')"
            @keyup="capsLock = $event.getModifierState('CapsLock')"
            @blur="capsLock = false"
          />
          <input
            v-else
            id="login-token"
            v-model="token"
            name="identity-token"
            :type="showSecret ? 'text' : 'password'"
            autocomplete="off"
            spellcheck="false"
            autocapitalize="none"
            maxlength="256"
            required
            :disabled="busy"
            placeholder="粘贴个人身份令牌"
          />
          <button
            type="button"
            :aria-label="showSecret ? '隐藏凭据' : '显示凭据'"
            :aria-pressed="showSecret"
            :disabled="busy"
            @click="showSecret = !showSecret"
          >
            {{ showSecret ? '隐藏' : '显示' }}
          </button>
        </div>
        <p v-if="capsLock" class="login-hint">大写锁定已开启</p>
        <p v-if="mode === 'token'" class="login-hint">
          在「账户安全」创建个人身份令牌。浏览器选片助手的采集令牌不能用于登录。
        </p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <p v-else-if="route.query.revoked === '1'" class="login-hint" role="status">
          当前设备的身份令牌已撤销，请重新登录。
        </p>
        <button class="primary" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
        <button
          v-if="passkeyAvailable"
          class="passkey-login"
          type="button"
          :disabled="busy"
          @click="passkeyLogin"
        >
          使用通行密钥
        </button>
        <details v-else-if="capabilities?.enabled" class="login-note">
          <summary>通行密钥登录</summary>
          <p>
            {{
              capabilities.reason || '请使用支持通行密钥的浏览器，通过 HTTPS 或 localhost 访问。'
            }}
          </p>
        </details>
        <details class="login-note">
          <summary>首次登录或忘记密码</summary>
          <p>
            默认管理员为 admin。首次启动时，终端会显示初始密码；也可在部署机器的 .env 文件中查看
            TREASURE_ADMIN_PASSWORD。修改过密码后，需使用新密码。
          </p>
          <p>
            忘记密码时，在部署目录运行
            <code>docker compose run --rm api python -m app.cli reset-password admin</code
            >，按提示设置新密码。
          </p>
        </details>
        <RouterLink
          v-if="display.allow_guest_access"
          class="guest-link"
          :to="loginDestination(route.query.next)"
          >先浏览视频库</RouterLink
        >
      </template>
      <p v-if="statusError" class="form-error" role="alert">
        {{ statusError }}
        <button type="button" class="text-button" @click="checkStatus">重试</button>
      </p>
    </form>
  </main>
</template>
<style scoped>
.login-card {
  width: 430px;
}
.login-card h1 {
  margin-bottom: 8px;
}
.login-intro {
  color: #9499a0;
  margin: 0 0 26px;
}
.login-methods {
  display: flex;
  gap: 24px;
  border-bottom: 1px solid #e9ebee;
  margin-bottom: 24px;
}
.login-methods button {
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 10px 0;
  background: transparent;
  color: #9499a0;
  font-size: 14px;
}
.login-methods .selected {
  color: #24282d;
  border-bottom-color: #24282d;
}
.login-field {
  display: block;
  color: #61666d;
  font-size: 13px;
  margin-top: 20px;
}
.secret-field {
  position: relative;
  margin-top: 8px;
}
.secret-field input {
  margin: 0;
  padding-right: 60px;
}
.secret-field button {
  position: absolute;
  right: 5px;
  top: 50%;
  transform: translateY(-50%);
  border: 0;
  background: transparent;
  color: #777f86;
  font-size: 12px;
  padding: 6px 9px;
}
.login-hint {
  color: #9499a0;
  line-height: 1.65;
  margin: 9px 0 0;
  font-size: 12px !important;
}
.passkey-login {
  width: 100%;
  margin-top: 10px;
}
.login-note {
  margin-top: 20px;
}
.login-note summary {
  cursor: pointer;
}
.login-note code,
.setup-note code {
  overflow-wrap: anywhere;
  font-size: 11px;
}
.guest-link {
  display: block;
  text-align: center;
  margin-top: 24px;
  color: #61666d;
  font-size: 13px;
}
.setup-note {
  border-top: 1px solid #e9ebee;
  padding-top: 18px;
  font-size: 13px;
  line-height: 1.8;
}
.setup-note h2 {
  font-size: 15px;
  margin: 0;
}
.setup-note ol {
  padding-left: 20px;
  color: #61666d;
}
.setup-note li {
  padding-bottom: 9px;
}
</style>
