<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { api, write, date } from '../api';
import {
  getPasskeyCapabilities,
  passkeyError,
  registerPasskey,
  supportsPasskeys,
} from '../auth/passkeys';
import type { PasskeyCapabilities, SavedPasskey } from '../auth/passkeys';

const items = ref<SavedPasskey[]>([]);
const capabilities = ref<PasskeyCapabilities | null>(null);
const busy = ref(false),
  loading = ref(true),
  error = ref(''),
  message = ref('');
const name = ref(''),
  password = ref(''),
  adding = ref(false),
  removing = ref<SavedPasskey | null>(null);
const available = computed(() => supportsPasskeys() && capabilities.value?.available);
const unavailableReason = computed(() =>
  !supportsPasskeys()
    ? '请使用支持通行密钥的浏览器，并通过 HTTPS 或 localhost 访问。'
    : capabilities.value?.reason,
);

async function load() {
  const [saved, info] = await Promise.all([
    api<{ items: SavedPasskey[] }>('/auth/passkeys'),
    getPasskeyCapabilities(),
  ]);
  items.value = saved.items;
  capabilities.value = info;
}
onMounted(async () => {
  try {
    await load();
  } catch (e) {
    error.value = passkeyError(e);
  } finally {
    loading.value = false;
  }
});
function closeForm() {
  adding.value = false;
  removing.value = null;
  password.value = '';
}
async function save() {
  busy.value = true;
  error.value = '';
  message.value = '';
  try {
    if (removing.value) {
      await write(`/auth/passkeys/${removing.value.id}`, { password: password.value }, 'DELETE');
      message.value = '已移除通行密钥';
    } else {
      await registerPasskey(password.value, name.value.trim() || '我的通行密钥');
      message.value = '通行密钥已添加，下次可直接验证登录';
    }
    closeForm();
    await load();
  } catch (e) {
    error.value = passkeyError(e);
  } finally {
    password.value = '';
    busy.value = false;
  }
}
</script>

<template>
  <main class="security-page">
    <RouterLink to="/" class="muted">返回视频库</RouterLink>
    <header>
      <h1>账户安全</h1>
      <p>使用设备锁屏、指纹或面容登录。</p>
    </header>
    <section class="security-panel">
      <div class="security-heading">
        <h2>通行密钥</h2>
        <button
          class="primary"
          :disabled="!available || busy || loading"
          @click="
            closeForm();
            adding = true;
            error = '';
            message = '';
          "
        >
          添加通行密钥
        </button>
      </div>
      <p v-if="unavailableReason" class="muted">{{ unavailableReason }}</p>
      <p v-if="loading" role="status">正在读取…</p>
      <p v-else-if="!items.length" class="security-empty">还没有通行密钥，密码登录仍可使用。</p>
      <ul v-else class="passkey-list">
        <li v-for="item in items" :key="item.id">
          <div>
            <strong>{{ item.name }}</strong
            ><span>{{
              item.last_used_at
                ? `上次使用 ${date(item.last_used_at)}`
                : `添加于 ${date(item.created_at)}`
            }}</span>
          </div>
          <button
            class="text-button"
            :disabled="busy"
            @click="
              closeForm();
              removing = item;
              error = '';
              message = '';
            "
          >
            移除
          </button>
        </li>
      </ul>
      <form v-if="adding || removing" class="passkey-form" @submit.prevent="save">
        <h3>{{ removing ? `移除「${removing.name}」` : '添加通行密钥' }}</h3>
        <label v-if="adding"
          >名称<input
            v-model="name"
            maxlength="100"
            placeholder="如：MacBook 或手机"
            autocomplete="off"
        /></label>
        <label
          >当前密码<input
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            maxlength="256"
        /></label>
        <p class="muted">确认身份后继续。</p>
        <div class="security-actions">
          <button class="primary" :disabled="busy">
            {{ busy ? '正在验证…' : removing ? '确认移除' : '继续' }}</button
          ><button type="button" :disabled="busy" @click="closeForm">取消</button>
        </div>
      </form>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <p v-if="message" class="security-message" role="status">{{ message }}</p>
    </section>
  </main>
</template>

<style scoped>
.security-page {
  max-width: 760px;
  margin: 36px auto;
  padding: 0 24px;
}
header {
  margin: 28px 0;
}
h1 {
  margin-bottom: 8px;
}
header p {
  color: #777;
}
.security-panel {
  padding: 24px;
  border: 1px solid #e7e9ed;
  border-radius: 10px;
}
.security-heading,
.security-actions,
.passkey-list li {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
}
.security-heading h2 {
  margin: 0;
  font-size: 18px;
}
.security-empty {
  padding: 24px 0 6px;
  color: #777;
}
.passkey-list {
  list-style: none;
  margin: 20px 0 0;
  padding: 0;
}
.passkey-list li {
  padding: 18px 0;
  border-top: 1px solid #eee;
}
.passkey-list span {
  display: block;
  color: #8b9099;
  font-size: 12px;
  margin-top: 6px;
}
.passkey-form {
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid #eee;
}
.passkey-form label {
  display: grid;
  gap: 8px;
  margin-top: 16px;
}
.security-actions {
  justify-content: flex-start;
}
.security-message {
  color: #278354;
}
@media (max-width: 520px) {
  .security-page {
    padding: 0 16px;
  }
  .security-panel {
    padding: 18px;
  }
  .security-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
