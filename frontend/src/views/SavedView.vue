<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, write, query, errorText } from '../api';
import type { Page } from '../types';
import type { PersonalList, SavedVideo } from '../utils/personalLists';
import VideoCard from '../components/VideoCard.vue';
import UiIcon from '../components/UiIcon.vue';
import EmptyState from '../components/EmptyState.vue';
import Pagination from '../components/Pagination.vue';
import PlaylistStartButton from '../player/PlaylistStartButton.vue';
const route = useRoute(),
  router = useRouter();
const lists = ref<PersonalList[]>([]),
  items = ref<SavedVideo[]>([]);
const active = computed(
  () => lists.value.find((list) => list.id === route.query.list) || lists.value[0],
);
const q = ref(''),
  watched = ref('all'),
  page = ref(1),
  total = ref(0);
const loading = ref(true),
  saving = ref(false),
  error = ref(''),
  notice = ref('');
const listDialog = ref<HTMLDialogElement>(),
  itemDialog = ref<HTMLDialogElement>();
const editingListId = ref(''),
  itemPlaylistId = ref('');
const editing = ref(false),
  deletePrompt = ref(false),
  name = ref(''),
  description = ref('');
const selected = ref<SavedVideo>(),
  note = ref(''),
  target = ref('');
let generation = 0,
  controller = new AbortController();
async function refreshLists() {
  lists.value = (await api<{ items: PersonalList[] }>('/me/playlists')).items;
}
async function load(p = 1) {
  if (!active.value) return;
  const n = ++generation;
  controller.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = '';
  page.value = p;
  try {
    const data = await api<Page<SavedVideo>>(
      `/me/playlists/${active.value.id}/videos?${query({ page: p, page_size: 24, q: q.value, watched: watched.value })}`,
      { signal: controller.signal },
    );
    if (n !== generation) return;
    items.value = data.items;
    total.value = data.total;
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) loading.value = false;
  }
}
function choose(id: string) {
  void router.replace({ path: '/saved', query: { list: id } });
}
function editList(existing = false) {
  if (saving.value) return;
  editing.value = existing;
  editingListId.value = existing ? active.value?.id || '' : '';
  deletePrompt.value = false;
  error.value = '';
  name.value = existing ? active.value?.name || '' : '';
  description.value = existing ? active.value?.description || '' : '';
  listDialog.value?.showModal();
}
async function changeList(remove = false) {
  if (saving.value || (!remove && !name.value.trim())) return;
  const id = editingListId.value;
  if (editing.value && !id) return;
  saving.value = true;
  error.value = '';
  try {
    const data = await write<PersonalList>(
      `/me/playlists${editing.value && id ? '/' + id : ''}`,
      { name: name.value.trim(), description: description.value.trim() },
      remove ? 'DELETE' : editing.value ? 'PATCH' : 'POST',
    );
    await refreshLists();
    listDialog.value?.close();
    choose(remove ? lists.value[0]!.id : data.id);
    notice.value = remove ? '片单已删除，视频归档仍保留' : '片单已保存';
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
function editItem(video: SavedVideo) {
  if (saving.value) return;
  itemPlaylistId.value = active.value?.id || '';
  selected.value = video;
  note.value = video.playlist_item.note || '';
  target.value = '';
  error.value = '';
  itemDialog.value?.showModal();
}
async function changeItem(video: SavedVideo, operation: 'watched' | 'remove' | 'note' | 'move') {
  const id = operation === 'watched' ? active.value?.id : itemPlaylistId.value;
  if (!id || saving.value) return;
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const base = `/me/playlists/${id}/videos/${video.id}`;
    if (operation === 'move') await write(`${base}/move`, { target_playlist_id: target.value });
    else
      await write(
        base,
        operation === 'watched'
          ? { watched: !video.playlist_item.watched }
          : operation === 'note'
            ? { note: note.value }
            : {},
        operation === 'remove' ? 'DELETE' : 'PUT',
      );
    itemDialog.value?.close();
    await refreshLists();
    await load(operation === 'remove' || operation === 'move' ? 1 : page.value);
    notice.value =
      operation === 'remove'
        ? '已从片单移除，视频归档仍保留'
        : operation === 'move'
          ? '已移入目标片单'
          : '已保存';
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
watch(
  () => active.value?.id,
  () => {
    listDialog.value?.close();
    itemDialog.value?.close();
    editingListId.value = '';
    itemPlaylistId.value = '';
    selected.value = undefined;
    q.value = '';
    watched.value = 'all';
    notice.value = '';
    void load();
  },
);
onMounted(async () => {
  try {
    await refreshLists();
  } catch (e) {
    error.value = errorText(e);
    loading.value = false;
  }
});
onBeforeUnmount(() => {
  generation++;
  controller.abort();
});
</script>
<template>
  <main class="content-shell saved-shell">
    <header class="page-heading">
      <div>
        <h1>我的片单</h1>
        <p class="muted">想看的留到稍后，喜欢的分门别类。</p>
      </div>
      <button @click="editList()">＋ 新建片单</button>
    </header>
    <div class="saved-layout">
      <nav class="saved-navigation" aria-label="我的片单">
        <button
          v-for="list in lists"
          :key="list.id"
          :class="{ active: active?.id === list.id }"
          :aria-current="active?.id === list.id ? 'page' : undefined"
          @click="choose(list.id)"
        >
          <UiIcon name="folder" /><span>{{ list.name }}</span
          ><small>{{ list.item_count }}</small>
        </button>
      </nav>
      <section class="saved-content">
        <header v-if="active" class="saved-heading">
          <div>
            <h2>{{ active.name }}</h2>
            <p class="muted">
              {{ active.item_count }} 个视频 · {{ active.unwatched_count }} 个未看
            </p>
            <p v-if="active.description" class="list-description">{{ active.description }}</p>
          </div>
          <div class="saved-heading-actions">
            <PlaylistStartButton
              v-if="active.item_count"
              :scope="{ type: 'personal', id: active.id }"
            /><button v-if="active.kind !== 'watch_later'" @click="editList(true)">编辑片单</button>
          </div>
        </header>
        <form class="saved-filters" @submit.prevent="load()">
          <input v-model="q" aria-label="在片单中搜索" placeholder="搜索片单内的视频" /><button>
            搜索</button
          ><select v-model="watched" aria-label="观看状态" @change="load()">
            <option value="all">全部视频</option>
            <option value="unwatched">还没看过</option>
            <option value="watched">已经看过</option>
          </select>
        </form>
        <p v-if="notice" class="saved-notice" role="status">{{ notice }}</p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div v-if="loading" class="loading-block">正在读取片单…</div>
        <div v-else-if="items.length && active" class="video-grid saved-grid">
          <div v-for="video in items" :key="video.id" class="saved-item">
            <VideoCard :video="video" :playlist="{ type: 'personal', id: active.id }" />
            <p v-if="video.playlist_item.note" class="saved-note">{{ video.playlist_item.note }}</p>
            <div class="saved-item-actions">
              <button
                :disabled="saving"
                :aria-pressed="video.playlist_item.watched"
                @click="changeItem(video, 'watched')"
              >
                {{ video.playlist_item.watched ? '✓ 已看过' : '标为看过' }}</button
              ><button @click="editItem(video)" aria-haspopup="dialog">整理</button>
            </div>
          </div>
        </div>
        <EmptyState
          v-else-if="!error"
          :title="q || watched !== 'all' ? '没有匹配的视频' : '片单还是空的'"
          text="在视频卡片或播放页点击“存入片单”，就能收在这里。"
          ><RouterLink to="/" class="outline-link">去视频库看看</RouterLink></EmptyState
        >
        <Pagination :page="page" :total="total" @change="load" />
      </section>
    </div>
    <dialog ref="listDialog" class="library-dialog" :aria-label="editing ? '编辑片单' : '新建片单'">
      <form @submit.prevent="changeList()">
        <div class="dialog-heading">
          <h2>{{ editing ? '编辑片单' : '新建片单' }}</h2>
          <button type="button" class="dialog-close" aria-label="关闭" @click="listDialog?.close()">
            ×
          </button>
        </div>
        <label>片单名称<input v-model="name" maxlength="100" required autofocus /></label
        ><label
          >简介<textarea v-model="description" maxlength="1000" rows="3" placeholder="可留空" />
        </label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <p v-if="deletePrompt">删除“{{ active?.name }}”？其中的视频归档不会删除。</p>
        <div class="dialog-footer">
          <button
            v-if="editing"
            type="button"
            class="text-button danger"
            :disabled="saving"
            @click="deletePrompt ? changeList(true) : (deletePrompt = true)"
          >
            {{ deletePrompt ? '确认删除片单' : '删除片单' }}</button
          ><button class="primary" :disabled="saving || !name.trim()">保存</button>
        </div>
      </form>
    </dialog>
    <dialog ref="itemDialog" class="library-dialog" aria-label="整理视频">
      <template v-if="selected"
        ><div class="dialog-heading">
          <h2>整理视频</h2>
          <button class="dialog-close" aria-label="关闭" @click="itemDialog?.close()">×</button>
        </div>
        <p class="dialog-subtitle">{{ selected.title }}</p>
        <form @submit.prevent="changeItem(selected!, 'note')">
          <label
            >我的备注<textarea
              v-model="note"
              maxlength="2000"
              rows="4"
              placeholder="记下想回看的片段或自己的想法"
            /></label
          ><button :disabled="saving" class="primary">保存备注</button>
        </form>
        <div class="move-list-form">
          <select v-model="target" aria-label="移入片单">
            <option value="" disabled>移入其他片单</option>
            <option
              v-for="list in lists.filter((l) => l.id !== active?.id)"
              :key="list.id"
              :value="list.id"
            >
              {{ list.name }}
            </option></select
          ><button :disabled="saving || !target" @click="changeItem(selected, 'move')">移动</button>
        </div>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="dialog-footer">
          <button
            class="text-button danger"
            :disabled="saving"
            @click="changeItem(selected, 'remove')"
          >
            从当前片单移除
          </button>
        </div></template
      >
    </dialog>
  </main>
</template>
