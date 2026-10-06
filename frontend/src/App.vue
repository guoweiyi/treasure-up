<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { session, sessionRevision, display, write, errorText } from './api';
import UiIcon from './components/UiIcon.vue';
import { inNativeShell } from './client';
import { requiresLogin } from './utils/accessPolicy';
const route = useRoute(),
  router = useRouter();
const error = ref('');
const busy = ref(false);
const mobileAccount = ref<HTMLDetailsElement>();
const nativeShell = inNativeShell();
const search = ref('');
watch(
  () => route.query.q,
  (value) => {
    search.value = String(value || '');
  },
  { immediate: true },
);
function searchVideos() {
  void router.push({ path: '/', query: search.value.trim() ? { q: search.value.trim() } : {} });
}
watch(
  () => route.fullPath,
  () => {
    if (mobileAccount.value) mobileAccount.value.open = false;
  },
);
const isAdmin = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
watch(
  () => display.site_name,
  (name) => {
    document.title = `${name} · 私人视频库`;
  },
  { immediate: true },
);
watch(
  () => [session.user, display.allow_guest_access],
  () => {
    if (
      !session.user &&
      session.ready &&
      requiresLogin(route.path, !!route.meta.login, display.allow_guest_access)
    )
      void router.replace({ path: '/login', query: { next: route.fullPath } });
  },
);
async function logout() {
  busy.value = true;
  error.value = '';
  try {
    await write('/auth/logout');
    session.user = null;
    session.csrf = '';
    await router.push('/');
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <header v-if="route.path !== '/login'" class="topbar">
    <RouterLink class="brand" to="/" :aria-label="`${display.site_name} 首页`"
      ><span class="brand-mark"><UiIcon name="play" /></span
      ><span>{{ display.site_name }}</span></RouterLink
    >
    <nav aria-label="主导航">
      <RouterLink
        to="/"
        :class="{ active: route.path === '/' || route.path.startsWith('/videos/') }"
        >首页</RouterLink
      ><RouterLink to="/creators" :class="{ active: route.path.startsWith('/creators') }"
        >UP 主</RouterLink
      ><RouterLink to="/collections">收藏夹</RouterLink
      ><RouterLink to="/saved">我的片单</RouterLink>
    </nav>
    <form class="header-search" role="search" @submit.prevent="searchVideos">
      <input v-model="search" placeholder="搜索视频库" aria-label="搜索视频库" /><button
        aria-label="搜索"
        type="submit"
      >
        <UiIcon name="search" />
      </button>
    </form>
    <div class="account-nav">
      <details v-if="session.user" ref="mobileAccount" class="mobile-account-menu">
        <summary :aria-label="`${session.user.username} 的账户菜单`">
          {{ session.user.username.slice(0, 1).toUpperCase() }}
        </summary>
        <div>
          <span>{{ session.user.username }}</span
          ><RouterLink to="/account/security">账户安全</RouterLink
          ><RouterLink v-if="isAdmin" to="/admin">管理中心</RouterLink
          ><a v-if="nativeShell" href="https://treasure-up.invalid/connect">切换服务器</a
          ><button :disabled="busy" @click="logout">退出登录</button>
        </div>
      </details>
      <RouterLink v-if="isAdmin" to="/admin" :class="{ active: route.path.startsWith('/admin') }"
        >管理中心</RouterLink
      ><RouterLink
        v-if="session.user"
        to="/account/security"
        class="account-avatar"
        aria-label="账户安全"
        title="账户安全"
        >{{ session.user.username.slice(0, 1).toUpperCase() }}</RouterLink
      ><span v-if="session.user" class="username">{{ session.user.username }}</span
      ><button v-if="session.user" class="text-button" :disabled="busy" @click="logout">
        退出
      </button>
      <RouterLink
        v-else
        class="login-link"
        :to="{ path: '/login', query: { next: route.fullPath } }"
        >登录</RouterLink
      >
      <a v-if="nativeShell" class="text-button" href="https://treasure-up.invalid/connect"
        >切换服务器</a
      >
    </div>
  </header>
  <div v-if="error" class="global-error" role="alert">{{ error }}</div>
  <RouterView :key="sessionRevision" />
  <nav
    v-if="route.path !== '/login' && !route.path.startsWith('/admin')"
    class="mobile-navigation"
    aria-label="底部导航"
  >
    <RouterLink to="/" :class="{ active: route.path === '/' || route.path.startsWith('/videos/') }"
      ><UiIcon name="play" /><span>视频</span></RouterLink
    >
    <RouterLink to="/creators" :class="{ active: route.path.startsWith('/creators') }"
      ><UiIcon name="user" /><span>UP 主</span></RouterLink
    >
    <RouterLink to="/collections"><UiIcon name="folder" /><span>收藏夹</span></RouterLink>
    <RouterLink to="/saved"><UiIcon name="star" /><span>我的片单</span></RouterLink>
  </nav>
  <a
    v-if="nativeShell && route.path === '/login'"
    class="native-connection-link"
    href="https://treasure-up.invalid/connect"
    >切换服务器</a
  >
  <footer v-if="route.path !== '/login' && !route.path.startsWith('/admin')" class="site-footer">
    {{ display.site_name }} <span>·</span> 私人视频库
  </footer>
</template>
<style scoped>
.login-link {
  background: #222;
  color: #fff;
  border-radius: 6px;
  padding: 7px 18px;
  white-space: nowrap;
}
.native-connection-link {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  color: #777;
  font-size: 13px;
}
@media (max-width: 700px) {
  .account-nav .account-avatar {
    display: none;
    width: 26px;
    height: 26px;
    flex-shrink: 0;
  }
  .account-nav {
    flex-wrap: wrap;
  }
}
</style>
