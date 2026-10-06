<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useRouter } from 'vue-router';
import { session, write, errorText } from '../api';
import type { User } from '../types';

const router = useRouter();
const changing = ref(false),
  busy = ref(false),
  error = ref('');
const currentPassword = ref(''),
  newPassword = ref(''),
  confirmPassword = ref('');
const canChange = computed(
  () =>
    !!currentPassword.value &&
    newPassword.value.length >= 12 &&
    newPassword.value === confirmPassword.value,
);
function clear() {
  currentPassword.value = newPassword.value = confirmPassword.value = '';
}
function toggle() {
  changing.value = !changing.value;
  error.value = '';
  clear();
}
async function changePassword() {
  if (busy.value || !canChange.value) return;
  busy.value = true;
  error.value = '';
  try {
    const data = await write<{ user: User; csrf_token: string }>('/auth/password', {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });
    session.user = data.user;
    session.csrf = data.csrf_token;
    session.ready = true;
    clear();
    changing.value = false;
    await router.replace({ path: '/account/security', query: { password_updated: '1' } });
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
onBeforeUnmount(clear);
</script>
<template>
  <section class="password-panel">
    <header>
      <div>
        <h2>登录密码</h2>
        <p>更新密码后，其他设备需重新登录，已添加的通行密钥保留。</p>
      </div>
      <button :disabled="busy" @click="toggle">{{ changing ? '取消修改' : '修改密码' }}</button>
    </header>
    <form v-if="changing" @submit.prevent="changePassword">
      <input
        :value="session.user?.username"
        autocomplete="username"
        class="password-username"
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
      <button class="primary" :disabled="busy || !canChange">
        {{ busy ? '正在保存…' : '保存新密码' }}
      </button>
    </form>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.password-panel {
  margin-top: 20px;
  padding: 24px;
  border: 1px solid #e7e9ed;
  border-radius: 8px;
}
header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
}
h2 {
  margin: 0;
  font-size: 18px;
}
header p {
  color: #8b9099;
  font-size: 12px;
  line-height: 1.7;
  margin: 9px 0 0;
}
header > button {
  flex-shrink: 0;
}
form {
  margin-top: 24px;
  border-top: 1px solid #eee;
}
label {
  display: grid;
  gap: 8px;
  margin-top: 16px;
  font-size: 13px;
}
form > button {
  margin-top: 18px;
}
.password-username {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
@media (max-width: 520px) {
  .password-panel {
    padding: 18px;
  }
  header {
    flex-direction: column;
    gap: 14px;
  }
}
</style>
