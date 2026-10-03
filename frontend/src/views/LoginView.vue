<script setup lang="ts">
import { ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { session, write, errorText, loadDisplaySettings } from '../api';
const username = ref(''),
  password = ref(''),
  busy = ref(false),
  error = ref('');
const router = useRouter(),
  route = useRoute();
async function login() {
  busy.value = true;
  error.value = '';
  try {
    const data = await write('/auth/login', { username: username.value, password: password.value });
    session.user = data.user;
    session.csrf = data.csrf_token;
    session.ready = true;
    password.value = '';
    await loadDisplaySettings();
    const next = String(route.query.next || '/');
    await router.replace(next.startsWith('/') && !next.startsWith('//') ? next : '/');
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
      <div class="brand"><span class="brand-mark">T</span><span>Treasure Up</span></div>
      <h1>回到你的收藏</h1>
      <p class="muted">登录后浏览已保存的视频、弹幕和评论。</p>
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
      <p class="login-note">使用本站账号登录。B 站账号在管理后台单独配置。</p>
    </form>
  </main>
</template>
