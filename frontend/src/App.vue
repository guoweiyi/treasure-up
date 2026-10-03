<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { session, display, write, errorText } from './api';
import UiIcon from './components/UiIcon.vue';
const route = useRoute(),
  router = useRouter();
const error = ref('');
const busy = ref(false);
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
const isAdmin = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
watch(
  () => display.site_name,
  (name) => {
    document.title = `${name} · 私人视频库`;
  },
  { immediate: true },
);
watch(
  () => session.user,
  (user) => {
    if (!user && session.ready && route.path !== '/login')
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
    await router.push('/login');
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <header v-if="session.user && route.path !== '/login'" class="topbar">
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
      ><RouterLink to="/collections">收藏夹</RouterLink>
    </nav>
    <form class="header-search" role="search" @submit.prevent="searchVideos">
      <input v-model="search" placeholder="搜索你收藏的视频" aria-label="搜索视频库" /><button
        aria-label="搜索"
        type="submit"
      >
        <UiIcon name="search" />
      </button>
    </form>
    <div class="account-nav">
      <RouterLink v-if="isAdmin" to="/admin" :class="{ active: route.path.startsWith('/admin') }"
        >管理中心</RouterLink
      ><span class="account-avatar" aria-hidden="true">{{
        session.user.username.slice(0, 1).toUpperCase()
      }}</span
      ><span class="username">{{ session.user.username }}</span
      ><button class="text-button" :disabled="busy" @click="logout">退出</button>
    </div>
  </header>
  <div v-if="error" class="global-error" role="alert">{{ error }}</div>
  <RouterView />
  <footer v-if="route.path !== '/login' && !route.path.startsWith('/admin')" class="site-footer">
    {{ display.site_name }} <span>·</span> 私人视频收藏
  </footer>
</template>
