<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { api, write, query, errorText, duration, date } from '../api';
import type { Creator } from '../types';

type PaidFilter = 'all' | 'only' | 'exclude';
type LatestVideo = {
  bvid: string;
  title: string;
  published_at?: string;
  duration: number;
  cover_url?: string;
  charging_exclusive: boolean;
  video_id?: string | null;
  capture_status?: string | null;
};
type LatestPage = {
  items: LatestVideo[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
  next_page?: number | null;
  cached: boolean;
};
type CaptureOptions = {
  accounts: { id: string; name: string; status: string }[];
  default_account_id?: string | null;
};
const props = defineProps<{ creator: Creator }>();
const emit = defineEmits<{ captured: [] }>();
const dialog = ref<HTMLDialogElement>();
const options = ref<CaptureOptions | null>(null),
  accountId = ref(''),
  paid = ref<PaidFilter>('all');
const data = ref<LatestPage | null>(null),
  selected = ref<string[]>([]),
  failedCovers = ref(new Set<string>());
const loading = ref(false),
  submitting = ref(false),
  error = ref(''),
  message = ref('');
let generation = 0;
let controller: AbortController | undefined;
const hasAccounts = computed(() => !!options.value?.accounts.length);
const candidates = computed(
  () => data.value?.items.filter((item) => item.capture_status !== 'complete') ?? [],
);
const allSelected = computed(
  () =>
    !!candidates.value.length &&
    candidates.value.every((item) => selected.value.includes(item.bvid)),
);
function resetRead() {
  generation++;
  controller?.abort();
}
async function open() {
  resetRead();
  const n = generation;
  options.value = null;
  data.value = null;
  selected.value = [];
  paid.value = 'all';
  error.value = message.value = '';
  loading.value = true;
  await nextTick();
  if (n !== generation) return;
  dialog.value?.showModal();
  controller = new AbortController();
  try {
    const result = await api<CaptureOptions>(`/creators/${props.creator.id}/capture-options`, {
      signal: controller.signal,
    });
    if (n !== generation) return;
    options.value = result;
    accountId.value = result.default_account_id || result.accounts[0]?.id || '';
    if (accountId.value) await latest(1);
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) loading.value = false;
  }
}
function close() {
  if (submitting.value) return;
  resetRead();
  loading.value = false;
  dialog.value?.close();
}
async function latest(page = 1, refresh = false) {
  if (submitting.value || !accountId.value) return;
  resetRead();
  const n = generation;
  controller = new AbortController();
  loading.value = true;
  error.value = message.value = '';
  data.value = null;
  selected.value = [];
  failedCovers.value = new Set();
  try {
    const result = await api<LatestPage>(
      `/creators/${props.creator.id}/latest?${query({ account_id: accountId.value, page, paid: paid.value, refresh })}`,
      { signal: controller.signal },
    );
    if (n === generation) data.value = result;
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) loading.value = false;
  }
}
async function capture() {
  if (submitting.value || loading.value || !selected.value.length) return;
  const n = generation;
  submitting.value = true;
  error.value = message.value = '';
  try {
    const result = await write<{ queued: number; reused: number; skipped: number }>(
      `/creators/${props.creator.id}/capture`,
      { account_id: accountId.value, bvids: [...selected.value], paid: paid.value },
    );
    if (n !== generation) return;
    selected.value = [];
    message.value = `已加入采集 ${result.queued} 个视频${result.reused ? `，${result.reused} 个已有任务` : ''}${result.skipped ? `，${result.skipped} 个无需重复采集` : ''}。稍后可在这位 UP 的已保存视频中查看。`;
    emit('captured');
  } catch (e) {
    if (n === generation) error.value = errorText(e);
  } finally {
    if (n === generation) submitting.value = false;
  }
}
watch(
  () => props.creator.id,
  () => {
    resetRead();
    dialog.value?.close();
    submitting.value = loading.value = false;
  },
);
onBeforeUnmount(() => {
  resetRead();
  dialog.value?.close();
});
</script>
<template>
  <button type="button" class="capture-trigger" aria-haspopup="dialog" @click="open">
    采集投稿
  </button>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="creator-capture-dialog"
      aria-labelledby="capture-title"
      @cancel.prevent="close"
      @click.self="close"
    >
      <header class="capture-heading">
        <div>
          <h2 id="capture-title">采集投稿</h2>
          <p>{{ creator.name }}<span>· 最新发布</span></p>
        </div>
        <button
          type="button"
          class="capture-close"
          :disabled="submitting"
          aria-label="关闭投稿采集"
          @click="close"
        >
          ×
        </button>
      </header>
      <div v-if="hasAccounts" class="capture-toolbar">
        <label
          >采集账号<select
            v-model="accountId"
            :disabled="loading || submitting"
            @change="latest(1)"
          >
            <option v-for="account in options?.accounts" :key="account.id" :value="account.id">
              {{ account.name }}
            </option>
          </select></label
        >
        <label
          >视频范围<select v-model="paid" :disabled="loading || submitting" @change="latest(1)">
            <option value="all">全部投稿</option>
            <option value="exclude">普通视频</option>
            <option value="only">仅充电视频</option>
          </select></label
        >
        <button type="button" :disabled="loading || submitting" @click="latest(1, true)">
          刷新投稿
        </button>
      </div>
      <p v-if="options && !hasAccounts" class="capture-empty">
        尚无可用采集账号，请联系管理员在管理中心添加并验证 B 站账号。
      </p>
      <p v-if="loading" class="capture-empty" role="status">正在读取 B 站投稿…</p>
      <div v-else-if="data" class="capture-results">
        <div v-if="data.items.length" class="capture-selection">
          <label
            ><input
              type="checkbox"
              :checked="allSelected"
              :disabled="submitting || !candidates.length"
              @change="selected = allSelected ? [] : candidates.map((item) => item.bvid)"
            />选择本页未完成的视频</label
          ><span>{{ data.items.length }} 个视频</span>
        </div>
        <ul class="capture-videos">
          <li v-for="item in data.items" :key="item.bvid">
            <label :class="{ archived: item.capture_status === 'complete' }"
              ><input
                v-model="selected"
                type="checkbox"
                :value="item.bvid"
                :disabled="submitting || item.capture_status === 'complete'"
                :aria-label="`选择 ${item.title}`"
              /><span class="capture-cover"
                ><img
                  v-if="item.cover_url && !failedCovers.has(item.bvid)"
                  :src="item.cover_url"
                  alt=""
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="failedCovers.add(item.bvid)"
                /><span v-else>暂无封面</span><time>{{ duration(item.duration) }}</time></span
              ><span class="capture-video-info"
                ><strong>{{ item.title }}</strong
                ><span class="capture-video-meta"
                  ><span v-if="item.charging_exclusive" class="capture-paid">充电专属</span
                  ><span v-if="item.published_at">{{ date(item.published_at) }}</span
                  ><span v-if="item.capture_status === 'complete'" class="capture-archived"
                    >已采集</span
                  ><span v-else-if="item.video_id">资料已保存</span></span
                ></span
              ></label
            >
          </li>
        </ul>
        <p v-if="!data.items.length" class="capture-empty">
          {{
            paid === 'only'
              ? '这一页没有充电视频。'
              : paid === 'exclude'
                ? '这一页没有普通视频。'
                : '这一页没有投稿。'
          }}<span v-if="data.has_more">可以继续查看下一页。</span>
        </p>
        <div class="capture-pagination">
          <span>第 {{ data.page }} 页<span v-if="paid !== 'all'"> · 每页筛选 30 条投稿</span></span>
          <div>
            <button
              type="button"
              :disabled="submitting || data.page <= 1"
              @click="latest(data.page - 1)"
            >
              上一页</button
            ><button
              type="button"
              :disabled="submitting || !data.has_more"
              @click="latest(data.next_page || data.page + 1)"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
      <p v-if="error" class="form-error" role="alert">
        {{ error }}
        <button
          type="button"
          class="text-button"
          :disabled="loading || submitting"
          @click="options ? latest() : open()"
        >
          重试
        </button>
      </p>
      <p v-if="message" class="capture-success" role="status">{{ message }}</p>
      <footer v-if="hasAccounts">
        <p>按所选账号已有权限采集，使用系统的画质与存储配置。</p>
        <button
          type="button"
          class="primary"
          :disabled="loading || submitting || !selected.length"
          @click="capture"
        >
          {{
            submitting
              ? '正在提交…'
              : selected.length
                ? `采集已选 ${selected.length} 个视频`
                : '先选择视频'
          }}
        </button>
      </footer>
    </dialog>
  </Teleport>
</template>
<style scoped>
.capture-trigger {
  padding: 7px 13px;
  font-size: 13px;
}
.creator-capture-dialog {
  width: min(760px, calc(100vw - 32px));
  max-height: min(820px, calc(100dvh - 48px));
  border: 1px solid #e2e5e9;
  border-radius: 8px;
  padding: 24px;
  color: #252b32;
  box-shadow: 0 16px 70px #0002;
}
.creator-capture-dialog::backdrop {
  background: #19202766;
}
.capture-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.capture-heading h2 {
  font-size: 19px;
  margin: 0;
}
.capture-heading p {
  font-size: 13px;
  margin: 9px 0 20px;
}
.capture-heading p span {
  margin-left: 9px;
  color: #9499a0;
}
.capture-close {
  border: 0;
  background: transparent;
  font-size: 24px;
  padding: 0 6px;
  color: #9499a0;
}
.capture-toolbar {
  display: flex;
  gap: 14px;
  align-items: flex-end;
  padding: 17px 0;
  border-top: 1px solid #eceef0;
  border-bottom: 1px solid #eceef0;
}
.capture-toolbar label {
  display: grid;
  gap: 7px;
  font-size: 12px;
  color: #9499a0;
  flex: 1;
}
.capture-toolbar select {
  width: 100%;
  color: #424951;
  font-size: 13px;
}
.capture-toolbar button {
  font-size: 12px;
  margin-bottom: 1px;
}
.capture-selection {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #9499a0;
  padding: 18px 0 8px;
}
.capture-selection label {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #61666d;
}
.capture-videos {
  list-style: none;
  padding: 0;
  margin: 0;
}
.capture-videos li {
  border-bottom: 1px solid #f0f1f3;
}
.capture-videos li > label {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 13px 0;
  cursor: pointer;
}
.capture-videos input,
.capture-selection input {
  width: 15px;
  height: 15px;
  accent-color: #34515b;
  flex-shrink: 0;
}
.capture-cover {
  position: relative;
  flex: 0 0 120px;
  width: 120px;
  aspect-ratio: 16/9;
  background: #f3f4f5;
  border-radius: 4px;
  overflow: hidden;
  display: grid;
  place-items: center;
  color: #9499a0;
  font-size: 11px;
}
.capture-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.capture-cover time {
  position: absolute;
  right: 4px;
  bottom: 3px;
  font-size: 10px;
  color: white;
  background: #0009;
  border-radius: 2px;
  padding: 1px 3px;
}
.capture-video-info {
  flex: 1;
  min-width: 0;
}
.capture-video-info strong {
  font-size: 14px;
  font-weight: 500;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
.capture-video-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
  font-size: 11px;
  line-height: 17px;
  color: #9499a0;
}
.capture-paid {
  background: #fff3e4;
  color: #9e6423;
  padding: 0 4px;
  border-radius: 3px;
}
.capture-archived {
  color: #668778;
}
.capture-empty {
  padding: 35px 0;
  color: #9499a0;
  text-align: center;
  font-size: 13px;
  line-height: 1.8;
}
.capture-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: 18px 0;
}
.capture-pagination > span {
  font-size: 12px;
  color: #9499a0;
}
.capture-pagination > div {
  display: flex;
  gap: 8px;
}
.capture-pagination button {
  font-size: 12px;
  padding: 6px 12px;
}
.creator-capture-dialog footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  border-top: 1px solid #eceef0;
  padding-top: 16px;
  margin-top: 16px;
}
.creator-capture-dialog footer p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: #9499a0;
}
.creator-capture-dialog footer button {
  flex-shrink: 0;
  font-size: 13px;
}
.capture-success {
  padding: 12px;
  background: #f2f7f4;
  font-size: 13px;
  line-height: 1.7;
  color: #50715d;
  border-radius: 4px;
}
@media (max-width: 600px) {
  .creator-capture-dialog {
    padding: 18px;
  }
  .capture-cover {
    flex-basis: 90px;
    width: 90px;
  }
  .capture-videos li > label {
    gap: 9px;
  }
  .capture-video-info strong {
    font-size: 13px;
  }
  .creator-capture-dialog footer {
    flex-direction: column;
    align-items: stretch;
  }
  .capture-toolbar {
    flex-wrap: wrap;
  }
  .capture-toolbar label {
    min-width: 100px;
  }
  .capture-toolbar > button {
    min-height: 36px;
  }
}
</style>
