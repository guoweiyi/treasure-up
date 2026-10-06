<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, errorText, statusText, session, sessionRevision, date } from '../api';
import { compactSpecification, measuredVariant, type VideoDetail } from '../player/mediaInfo';
import {
  playlistScope,
  playlistQuery,
  queueTarget,
  queuePreferenceKey,
  readQueuePreferences,
  type QueuePreferences,
  type QueueTarget,
} from '../player/queue';
import { usePlaylist, playlistPath, type PlaylistPage } from '../player/usePlaylist';
import PlaybackQueue from '../player/PlaybackQueue.vue';
import ArchivePlayer from '../components/ArchivePlayer.vue';
import CommentsPanel from '../components/CommentsPanel.vue';
import EmptyState from '../components/EmptyState.vue';
import RelatedVideos from '../components/RelatedVideos.vue';
import VideoUpCard from '../components/VideoUpCard.vue';
import SaveToList from '../components/SaveToList.vue';
import UiIcon from '../components/UiIcon.vue';
import ContentBadges from '../components/ContentBadges.vue';
import PartSelector from '../player/PartSelector.vue';
import PlaybackNodeMenu from '../player/PlaybackNodeMenu.vue';
import type { Playback } from '../types';
import { count } from '../utils/format';
import type { CommentSeek } from '../utils/commentTimeline';
const route = useRoute(),
  router = useRouter(),
  video = ref<VideoDetail | null>(null),
  partId = ref(''),
  activeVariantId = ref(''),
  heading = ref<HTMLElement>(),
  error = ref(''),
  actionError = ref('');
const player = ref<InstanceType<typeof ArchivePlayer>>(),
  playing = ref(false),
  autoStart = ref(false),
  resume = ref(true),
  transitioning = ref(false);
const preferences = ref<QueuePreferences>(readQueuePreferences(null));
const routing = ref<{ playback: Playback | null; routeId: string; busy: boolean }>({
  playback: null,
  routeId: '',
  busy: true,
});
const scope = computed(() => playlistScope(route.query));
const currentVideoId = computed(() => String(route.params.id));
const queue = usePlaylist(scope, currentVideoId, (scope, page, signal) =>
  api<PlaylistPage>(playlistPath(scope, page), { signal }),
);
const {
  items: queueItems,
  title: queueTitle,
  total: queueTotal,
  busy: queueBusy,
  error: queueError,
} = queue;
let seq = 0,
  controller = new AbortController();
let pendingNavigation: { id: string; start: boolean; resume: boolean } | null = null;
const part = computed(() => video.value?.parts?.find((p) => p.id === partId.value));
const commentPlayback = computed(() => ({
  currentPartId: partId.value,
  parts: (video.value?.parts || []).map((item) => ({
    id: item.id,
    position: item.position,
    duration: item.duration,
    playable: !!item.variants.length,
  })),
}));
const sourceUrl = computed(() =>
  /^BV[A-Za-z0-9]{10}$/.test(video.value?.bvid || '')
    ? `https://www.bilibili.com/video/${video.value!.bvid}/`
    : undefined,
);
let commentSeekGeneration = 0;
async function seekComment(value: CommentSeek) {
  const target = video.value?.parts?.find((item) => item.id === value.partId);
  if (
    !target?.variants.length ||
    !Number.isFinite(value.seconds) ||
    value.seconds < 0 ||
    value.seconds >= target.duration
  )
    return;
  const generation = ++commentSeekGeneration,
    videoId = currentVideoId.value,
    identity = sessionRevision.value;
  const continuePlaying = player.value?.playIntent() ?? playing.value;
  if (partId.value !== target.id) {
    selectPart(target.id);
    autoStart.value = continuePlaying;
    resume.value = false;
    await nextTick();
  }
  if (
    generation !== commentSeekGeneration ||
    videoId !== currentVideoId.value ||
    identity !== sessionRevision.value ||
    partId.value !== target.id
  )
    return;
  if (player.value?.seekTo(value.seconds, target.id, continuePlaying)) {
    heading.value?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }
}
const measured = computed(() =>
  measuredVariant(video.value?.media_properties, activeVariantId.value),
);
const archivedVariant = computed(() =>
  part.value?.variants.find((variant) => variant.id === activeVariantId.value),
);
const archiveSpecification = computed(() =>
  compactSpecification({ ...archivedVariant.value, ...measured.value }),
);
const canEdit = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
const description = computed(() => {
  const text = video.value?.description?.trim();
  return text && text !== '-' ? text : '';
});
const target = (direction: -1 | 1, ended = false) =>
  queueTarget(
    video.value?.parts || [],
    partId.value,
    queueItems.value,
    currentVideoId.value,
    direction,
    ended ? preferences.value.mode : undefined,
  );
const hasPrevious = computed(() => target(-1).kind !== 'stop');
const hasNext = computed(
  () =>
    target(1).kind !== 'stop' ||
    (!!scope.value &&
      queueItems.value.some((item) => item.id === currentVideoId.value) &&
      queueItems.value.length < queueTotal.value),
);
watch(
  () => session.user?.id,
  (id) => {
    try {
      preferences.value = readQueuePreferences(localStorage.getItem(queuePreferenceKey(id)));
    } catch {
      preferences.value = readQueuePreferences(null);
    }
  },
  { immediate: true },
);
function updatePreferences(value: Partial<QueuePreferences>) {
  preferences.value = { ...preferences.value, ...value };
  try {
    localStorage.setItem(queuePreferenceKey(session.user?.id), JSON.stringify(preferences.value));
  } catch {
    /* Optional browser storage. */
  }
}
function requestedPart(data: VideoDetail) {
  const parts = data.parts || [],
    requested = route.query.p || route.query.part;
  if (requested === 'last') return parts.filter((item) => item.variants.length).at(-1)?.id || '';
  return (
    parts.find((item) => item.id === requested || String(item.position) === requested)?.id ||
    parts.find((item) => item.variants.length)?.id ||
    parts[0]?.id ||
    ''
  );
}
async function load() {
  const n = ++seq,
    id = currentVideoId.value;
  controller.abort();
  controller = new AbortController();
  const navigation = pendingNavigation?.id === id ? pendingNavigation : null;
  pendingNavigation = null;
  autoStart.value = navigation?.start ?? preferences.value.autoStart;
  resume.value = navigation?.resume ?? true;
  video.value = null;
  actionError.value = '';
  activeVariantId.value = '';
  routing.value = { playback: null, routeId: '', busy: true };
  error.value = '';
  try {
    const data = await api<VideoDetail>(`/videos/${id}`, { signal: controller.signal });
    if (n !== seq) return;
    video.value = data;
    partId.value = requestedPart(data);
  } catch (e) {
    if (n === seq) error.value = errorText(e);
  }
}
function selectPart(id: string, automatic = false) {
  if (partId.value === id) return;
  autoStart.value = automatic || preferences.value.autoStart;
  resume.value = !automatic;
  partId.value = id;
  activeVariantId.value = '';
  const selected = video.value?.parts?.find((item) => item.id === id);
  const { part: _legacy, ...query } = route.query;
  void router.replace({ query: { ...query, p: String(selected?.position || id) } });
  if (!automatic && window.matchMedia('(max-width: 1024px)').matches)
    heading.value?.scrollIntoView({ block: 'start', behavior: 'smooth' });
}
async function selectVideo(id: string, automatic = false, last = false) {
  if (id === currentVideoId.value) return;
  pendingNavigation = { id, start: automatic || preferences.value.autoStart, resume: !automatic };
  await router.push({
    path: `/videos/${id}`,
    query: { ...playlistQuery(scope.value), ...(last ? { p: 'last' } : {}) },
  });
}
async function executeTarget(value: QueueTarget, automatic: boolean, direction: -1 | 1) {
  if (value.kind === 'part') selectPart(value.id, automatic);
  else if (value.kind === 'video') await selectVideo(value.id, automatic, direction === -1);
  else if (value.kind === 'replay') await player.value?.replay();
}
async function advance(direction: -1 | 1, automatic = false) {
  if (transitioning.value) return;
  const id = currentVideoId.value,
    currentPart = partId.value,
    key = seq,
    scopeKey = JSON.stringify(scope.value);
  transitioning.value = true;
  try {
    if (
      direction === 1 &&
      target(1, automatic).kind !== 'part' &&
      scope.value &&
      (!automatic || preferences.value.mode === 'continuous')
    )
      await queue.ensureNext();
    if (
      key !== seq ||
      id !== currentVideoId.value ||
      currentPart !== partId.value ||
      scopeKey !== JSON.stringify(scope.value)
    )
      return;
    if (automatic && !player.value?.isEnded(currentPart)) return;
    await executeTarget(target(direction, automatic), automatic, direction);
  } catch (e) {
    if (key === seq) actionError.value = errorText(e);
  } finally {
    transitioning.value = false;
  }
}
function ended(id: string) {
  if (id === partId.value) void advance(1, true);
}
watch(
  () => [route.query.p, route.query.part],
  () => {
    if (!video.value) return;
    const id = requestedPart(video.value);
    if (id !== partId.value) {
      autoStart.value = preferences.value.autoStart;
      resume.value = true;
      partId.value = id;
      activeVariantId.value = '';
    }
  },
);
watch(() => route.params.id, load, { immediate: true });
onBeforeUnmount(() => {
  seq++;
  controller.abort();
});
</script>
<template>
  <main class="content-shell watch-shell">
    <EmptyState v-if="error" title="视频暂时无法读取" :text="error" error
      ><button @click="load">重试</button></EmptyState
    >
    <div v-else-if="!video" class="loading-block">正在加载视频…</div>
    <div v-else class="watch-layout">
      <div class="watch-main">
        <header ref="heading" class="watch-heading">
          <h1>{{ video.title }}</h1>
          <ContentBadges :features="video.content_features" />
          <div class="video-meta">
            <span v-if="video.stats?.view != null"
              ><UiIcon name="play" />{{ count(video.stats.view) }} 播放</span
            >
            <span v-if="video.stats?.danmaku != null"
              ><UiIcon name="danmaku" />{{ count(video.stats.danmaku) }} 弹幕</span
            >
            <span v-if="video.published_at" :title="date(video.published_at)"
              >发布于
              <time :datetime="video.published_at">{{ date(video.published_at) }}</time></span
            >
            <span v-else :title="'归档时间：' + date(video.created_at)">发布时间未记录</span>
            <a
              v-if="sourceUrl"
              :href="sourceUrl"
              target="_blank"
              rel="noopener noreferrer"
              title="在哔哩哔哩打开"
              >{{ video.bvid }}</a
            >
            <span v-else>{{ video.bvid }}</span>
          </div>
        </header>
        <ArchivePlayer
          v-if="part?.variants?.length"
          ref="player"
          :key="video.id"
          :auto-start="autoStart"
          :resume="resume"
          :part="part"
          :poster="video.cover_url"
          :media-properties="video.media_properties"
          @variant="activeVariantId = $event"
          @ended="ended"
          @playing="playing = $event"
          @routing="routing = $event"
        />
        <div v-else class="unavailable-player">
          <UiIcon name="play" /><span>此分 P 还没有可播放的归档</span>
          <p>{{ statusText(video.capture_status) }}</p>
        </div>
        <div
          v-if="archivedVariant && archiveSpecification"
          class="video-specification"
          aria-label="当前播放规格"
        >
          <span>{{ archiveSpecification }}</span>
          <PlaybackNodeMenu
            :playback="routing.playback"
            :route-id="routing.routeId"
            :busy="routing.busy"
            @select="player?.selectRoute($event)"
          />
        </div>
        <div class="video-actionbar">
          <div
            class="source-statistics"
            :title="
              video.stats?.observed_at
                ? 'B 站统计快照：' + date(video.stats.observed_at)
                : 'B 站归档统计'
            "
          >
            <span v-if="video.stats?.like != null"
              ><UiIcon name="like" />{{ count(video.stats.like) }}</span
            >
            <span v-if="video.stats?.coin != null"
              ><UiIcon name="coin" />{{ count(video.stats.coin) }}</span
            >
            <span v-if="video.stats?.favorite != null"
              ><UiIcon name="folder" />{{ count(video.stats.favorite) }}</span
            >
            <span v-if="video.stats?.share != null"
              ><UiIcon name="share" />{{ count(video.stats.share) }}</span
            >
            <span v-if="video.stats?.observed_at" class="statistics-note">B 站统计快照</span>
          </div>
          <SaveToList
            :video-id="video.id"
            :title="video.title"
            :starred="video.starred"
            @change="video.starred = $event"
          />
          <RouterLink
            v-if="canEdit"
            :to="{ path: '/admin/videos', query: { edit: video.id } }"
            class="video-edit-link"
            >编辑标注</RouterLink
          >
        </div>
        <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
        <details
          v-if="
            video.source_state &&
            !['available', 'normal', 'active', 'unknown'].includes(video.source_state)
          "
          class="source-state-note archive-information"
        >
          <summary>归档信息</summary>
          <p>{{ statusText(video.source_state) }}，本地归档保留</p>
        </details>
        <details v-if="description" class="video-description" open>
          <summary>视频简介</summary>
          <p class="preserve-lines">{{ description }}</p>
        </details>
        <div v-if="video.tags?.length" class="tags">
          <RouterLink v-for="tag in video.tags" :key="tag" :to="{ path: '/', query: { tag } }">{{
            tag
          }}</RouterLink>
        </div>
        <div v-if="video.notes" class="personal-note">
          <h3>收藏笔记</h3>
          <p class="preserve-lines">{{ video.notes }}</p>
        </div>
        <p
          v-if="['partial', 'blocked', 'failed'].includes(video.capture_status)"
          class="inline-notice"
        >
          这条视频的归档{{ statusText(video.capture_status) }}，部分弹幕、评论或分 P 可能尚未保存。
        </p>
      </div>
      <aside class="watch-sidebar">
        <VideoUpCard :creators="video.creators" />
        <PlaybackQueue
          :items="queueItems"
          :current-id="video.id"
          :current-title="video.title"
          :part-title="part?.title"
          :part-position="part?.position"
          :part-count="video.parts?.length"
          :playing="playing"
          :title="queueTitle"
          :total="queueTotal"
          :busy="queueBusy"
          :error="queueError"
          :preferences="preferences"
          :previous="hasPrevious"
          :next="hasNext"
          :transitioning="transitioning"
          @preferences="updatePreferences"
          @previous="advance(-1)"
          @next="advance(1)"
          @select="selectVideo"
          @more="queueItems.some((item) => item.id === video?.id) ? queue.more() : queue.locate()"
        />

        <PartSelector
          v-if="video.parts && video.parts.length > 1"
          :parts="video.parts"
          :selected="partId"
          @select="selectPart"
        />
        <RelatedVideos :video="video" />
      </aside>
      <CommentsPanel
        class="watch-comments"
        :video-id="video.id"
        :playback="commentPlayback"
        @seek="seekComment"
      />
    </div>
  </main>
</template>

<style scoped>
.watch-layout {
  align-items: start;
  column-gap: 28px;
  row-gap: 0;
}
.watch-main {
  grid-column: 1;
  grid-row: 1;
}
.watch-sidebar {
  grid-column: 2;
  grid-row: 1 / span 2;
}
.watch-comments {
  grid-column: 1;
  grid-row: 2;
  min-width: 0;
}
.watch-sidebar :deep(.playback-queue) {
  margin: 18px 0;
}
@media (max-width: 950px) {
  .watch-layout {
    display: flex;
    flex-direction: column;
  }
  .watch-main,
  .watch-sidebar,
  .watch-comments {
    width: 100%;
  }
  .watch-sidebar {
    display: block;
    margin: 20px 0;
  }
  .watch-sidebar :deep(.related-videos) {
    display: none;
  }
}
</style>
