<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, write, session, errorText } from '../api';
import type { PersonalList } from '../utils/personalLists';
import UiIcon from './UiIcon.vue';
const props = defineProps<{
  videoId: string;
  title: string;
  starred?: boolean;
  compact?: boolean;
}>();
const emit = defineEmits<{ change: [starred: boolean] }>();
const router = useRouter(),
  route = useRoute();
const dialog = ref<HTMLDialogElement>();
const items = ref<PersonalList[]>([]),
  membership = ref<string[]>([]);
const loading = ref(false),
  saving = ref(false),
  error = ref(''),
  name = ref('');
const known = ref(false);
const saved = computed(() => (known.value ? membership.value.length > 0 : props.starred));
let generation = 0;
async function open() {
  if (saving.value) return;
  if (!session.user) {
    await router.push({ path: '/login', query: { next: route.fullPath } });
    return;
  }
  const n = ++generation;
  const videoId = props.videoId;
  items.value = [];
  membership.value = [];
  known.value = false;
  loading.value = true;
  error.value = '';
  name.value = '';
  await nextTick();
  if (n !== generation) return;
  dialog.value?.showModal();
  try {
    const [lists, selected] = await Promise.all([
      api<{ items: PersonalList[] }>('/me/playlists'),
      api<{ playlist_ids: string[] }>(`/me/videos/${videoId}/playlists`),
    ]);
    if (n !== generation) return;
    items.value = lists.items;
    membership.value = selected.playlist_ids;
    known.value = true;
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) loading.value = false;
  }
}
async function toggle(list: PersonalList) {
  if (saving.value) return;
  const n = generation,
    remove = membership.value.includes(list.id);
  saving.value = true;
  error.value = '';
  try {
    await write(`/me/playlists/${list.id}/videos/${props.videoId}`, {}, remove ? 'DELETE' : 'PUT');
    if (n !== generation) return;
    membership.value = remove
      ? membership.value.filter((id) => id !== list.id)
      : [...membership.value, list.id];
    list.item_count += remove ? -1 : 1;
    if (list.kind === 'watch_later') emit('change', !remove);
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) saving.value = false;
  }
}
async function create() {
  if (!name.value.trim() || saving.value || loading.value || !known.value) return;
  const n = generation;
  saving.value = true;
  error.value = '';
  try {
    const list = await write<PersonalList>('/me/playlists', { name: name.value.trim() });
    if (n !== generation) return;
    items.value.push(list);
    name.value = '';
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) saving.value = false;
  }
}
watch(
  () => props.videoId,
  () => {
    generation++;
    dialog.value?.close();
    known.value = false;
    saving.value = false;
  },
);
onBeforeUnmount(() => {
  generation++;
});
</script>
<template>
  <button
    class="save-list-button"
    :class="{ compact, saved }"
    :aria-label="`${saved ? '管理片单' : '存入片单'}：${title}`"
    aria-haspopup="dialog"
    @click="open"
  >
    <UiIcon name="folder" /><span v-if="!compact">{{ saved ? '已存入片单' : '存入片单' }}</span>
  </button>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="library-dialog"
      aria-label="存入我的片单"
      @cancel="saving && $event.preventDefault()"
      @click="!saving && $event.target === dialog && dialog?.close()"
    >
      <div class="dialog-heading">
        <h2>存入我的片单</h2>
        <button
          aria-label="关闭片单选择"
          class="dialog-close"
          :disabled="saving"
          @click="dialog?.close()"
        >
          ×
        </button>
      </div>
      <p class="dialog-subtitle">{{ title }}</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button v-if="error && !known" :disabled="loading || saving" @click="open">重新读取</button>
      <p v-if="loading" class="muted">正在读取片单…</p>
      <div v-else class="list-choices">
        <button
          v-for="list in items"
          :key="list.id"
          :aria-pressed="membership.includes(list.id)"
          :disabled="saving"
          @click="toggle(list)"
        >
          <UiIcon name="folder" /><span
            >{{ list.name }}<small>{{ list.item_count }} 个视频</small></span
          ><b aria-hidden="true">{{ membership.includes(list.id) ? '✓' : '+' }}</b>
        </button>
      </div>
      <form class="new-list-form" @submit.prevent="create">
        <input
          v-model="name"
          aria-label="新片单名称"
          placeholder="新建片单"
          maxlength="100"
        /><button :disabled="saving || loading || !known || !name.trim()">新建</button>
      </form>
      <div class="dialog-footer">
        <RouterLink to="/saved" @click="dialog?.close()">整理我的片单</RouterLink
        ><button class="primary" :disabled="saving" @click="dialog?.close()">完成</button>
      </div>
    </dialog>
  </Teleport>
</template>
