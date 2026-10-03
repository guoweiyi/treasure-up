<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { session, write, errorText, loadDisplaySettings, display } from '../api';
import UiIcon from '../components/UiIcon.vue';
import {
  getPasskeyCapabilities,
  loginWithPasskey,
  passkeyError,
  supportsPasskeys,
} from '../auth/passkeys';
import type { PasskeyCapabilities } from '../auth/passkeys';
const username = ref(''),
  password = ref(''),
  busy = ref(false),
  error = ref('');
const router = useRouter(),
  route = useRoute();
const capabilities = ref<PasskeyCapabilities | null>(null);
const passkeyAvailable = computed(() => supportsPasskeys() && capabilities.value?.available);
onMounted(async () => {
  try {
    capabilities.value = await getPasskeyCapabilities();
  } catch {
    /* Password login remains usable. */
  }
});
async function acceptLogin(data: any) {
  session.user = data.user;
  session.csrf = data.csrf_token;
  session.ready = true;
  password.value = '';
  await loadDisplaySettings();
  const next = String(route.query.next || '/');
  await router.replace(next.startsWith('/') && !next.startsWith('//') ? next : '/');
}
async function passkeyLogin() {
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
  busy.value = true;
  error.value = '';
  try {
    const data = await write('/auth/login', { username: username.value, password: password.value });
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
    <form class="login-card" @submit.prevent="login">
      <div class="brand">
        <span class="brand-mark"><UiIcon name="play" /></span><span>{{ display.site_name }}</span>
      </div>
      <h1>登录视频库</h1>
      <label
        >用户名<input
          v-model="username"
          name="username"
          autocomplete="username"
          required
          autofocus /></label
      ><label
        >密码<input
          v-model="password"
          name="password"
          type="password"
          autocomplete="current-password"
          required
      /></label>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
      <button v-if="passkeyAvailable" type="button" :disabled="busy" @click="passkeyLogin">
        使用通行密钥
      </button>
      <details v-else-if="capabilities?.enabled" class="login-note">
        <summary>通行密钥登录</summary>
        <p>
          {{ capabilities.reason || '请使用支持通行密钥的浏览器，通过 HTTPS 或 localhost 访问。' }}
        </p>
      </details>
    </form>
  </main>
</template>
