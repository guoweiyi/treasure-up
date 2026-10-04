<script setup lang="ts">
import { ref, onBeforeUnmount, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api, errorText } from '../api';
import { playlistPath, type PlaylistPage } from './usePlaylist';
import { playlistQuery, type PlaylistScope } from './queue';
const props = defineProps<{ scope: PlaylistScope }>();
const router = useRouter(),
  busy = ref(false),
  error = ref('');
let controller = new AbortController();
function reset() {
  controller.abort();
  controller = new AbortController();
  busy.value = false;
  error.value = '';
}
async function open() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  const scope = { ...props.scope },
    signal = controller.signal;
  try {
    const data = await api<PlaylistPage>(playlistPath(scope), { signal });
    if (signal.aborted) return;
    const first = data.items[0];
    if (!first) {
      error.value = '列表里还没有可播放的视频';
      return;
    }
    await router.push({ path: `/videos/${first.id}`, query: playlistQuery(scope) });
  } catch (e) {
    if (!signal.aborted) error.value = errorText(e);
  } finally {
    if (!signal.aborted) busy.value = false;
  }
}
watch(() => `${props.scope.type}:${props.scope.id}`, reset);
onBeforeUnmount(() => controller.abort());
</script>
<template>
  <div class="playlist-entry">
    <button class="outline-link" :disabled="busy" @click="open">
      {{ busy ? '正在读取…' : '播放列表' }}
    </button>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </div>
</template>
