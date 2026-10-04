<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, write, errorText, statusText, session, date } from '../api';
import { bitrate, formatSpecification, type VideoDetail } from '../player/mediaInfo';
import ArchivePlayer from '../components/ArchivePlayer.vue';
import CommentsPanel from '../components/CommentsPanel.vue';
import EmptyState from '../components/EmptyState.vue';
import RelatedVideos from '../components/RelatedVideos.vue';
import VideoUpCard from '../components/VideoUpCard.vue';
import UiIcon from '../components/UiIcon.vue';
import PartSelector from '../player/PartSelector.vue';
import { count } from '../utils/format';
const route = useRoute(),
  router = useRouter(),
  video = ref<VideoDetail | null>(null),
  partId = ref(''),
  activeVariantId = ref(''),
  heading = ref<HTMLElement>(),
  error = ref(''),
  actionError = ref(''),
  busy = ref(false);
let seq = 0;
const part = computed(() => video.value?.parts?.find((p) => p.id === partId.value));
const sourcePart = computed(() => video.value?.source_quality?.parts?.[partId.value]);
const maximum = computed(() => sourcePart.value?.maximum);
const measured = computed(() => video.value?.media_properties?.[activeVariantId.value]);
const archivedVariant = computed(() =>
  part.value?.variants.find((variant) => variant.id === activeVariantId.value),
);
const archiveSpecification = computed(() =>
  formatSpecification({ ...archivedVariant.value, fps: measured.value?.fps }),
);
const sourceObserved = computed(
  () => sourcePart.value?.observed_at || video.value?.source_quality?.observed_at,
);
const canEdit = computed(() => ['admin', 'editor'].includes(session.user?.role || ''));
const description = computed(() => {
  const text = video.value?.description?.trim();
  return text && text !== '-' ? text : '';
});
async function load() {
  const n = ++seq;
  video.value = null;
  actionError.value = '';
  activeVariantId.value = '';
  error.value = '';
  try {
    const data = await api<VideoDetail>(`/videos/${route.params.id}`);
    if (n !== seq) return;
    video.value = data;
    partId.value =
      data.parts?.find((p) => p.id === route.query.part)?.id || data.parts?.[0]?.id || '';
  } catch (e) {
    if (n === seq) error.value = errorText(e);
  }
}
async function star() {
  if (!video.value || busy.value) return;
  if (!session.user) {
    await router.push({ path: '/login', query: { next: route.fullPath } });
    return;
  }
  const current = video.value;
  busy.value = true;
  actionError.value = '';
  try {
    const result = await write<{ starred: boolean }>(
      `/videos/${current.id}/star`,
      { starred: !current.starred },
      'PUT',
    );
    if (video.value?.id === current.id) video.value.starred = result.starred;
  } catch (e) {
    if (video.value?.id === current.id) actionError.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
function selectPart(id: string) {
  if (partId.value === id) return;
  partId.value = id;
  activeVariantId.value = '';
  void router.replace({ query: { ...route.query, part: id } });
  if (window.matchMedia('(max-width: 1024px)').matches)
    heading.value?.scrollIntoView({ block: 'start', behavior: 'smooth' });
}
watch(
  () => route.query.part,
  (id) => {
    if (video.value?.parts?.some((item) => item.id === id)) partId.value = String(id);
  },
);
watch(() => route.params.id, load, { immediate: true });
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
            <span>{{ video.bvid }}</span>
          </div>
        </header>
        <ArchivePlayer
          v-if="part?.variants?.length"
          :key="video.id"
          :part="part"
          :poster="video.cover_url"
          :media-properties="video.media_properties"
          @variant="activeVariantId = $event"
        />
        <div v-else class="unavailable-player">
          <UiIcon name="play" /><span>此分 P 还没有可播放的归档</span>
          <p>{{ statusText(video.capture_status) }}</p>
        </div>
        <div class="video-specification" aria-label="画质与码率信息">
          <div>
            <span class="specification-label">最高可用</span
            ><strong>{{
              formatSpecification(maximum) ||
              (sourcePart?.status === 'unavailable' ? '本次未能获取' : '尚未探测')
            }}</strong>
          </div>
          <div v-if="archivedVariant">
            <span class="specification-label">当前归档</span
            ><span
              >{{ archiveSpecification || '原始规格'
              }}<template v-if="measured?.total_bitrate_bps">
                · {{ bitrate(measured.total_bitrate_bps) }}</template
              ><template v-else> · 码率未记录</template></span
            >
          </div>
          <details v-if="maximum || measured" class="specification-details">
            <summary>规格详情</summary>
            <dl>
              <template v-if="maximum"
                ><dt>可用规格范围</dt>
                <dd>
                  该账号最近探测<template v-if="sourceObserved">
                    · {{ date(sourceObserved) }}</template
                  >
                </dd>
                <dt>源视频码率</dt>
                <dd>{{ bitrate(maximum.video_bitrate_bps) }}（源站估算）</dd>
                <dt>最高音频</dt>
                <dd>
                  {{ sourcePart?.maximum_audio?.audio_codec || '未记录' }} ·
                  {{ bitrate(sourcePart?.maximum_audio?.audio_bitrate_bps) }}
                </dd></template
              >
              <template v-if="measured"
                ><dt>文件平均码率</dt>
                <dd>{{ bitrate(measured.total_bitrate_bps) }}（含封装开销）</dd>
                <dt>当前视频码率</dt>
                <dd>{{ bitrate(measured.video_bitrate_bps) }}</dd>
                <dt>当前音频码率</dt>
                <dd>{{ bitrate(measured.audio_bitrate_bps) }}</dd></template
              >
            </dl>
          </details>
        </div>
        <PartSelector
          v-if="video.parts && video.parts.length > 1"
          class="mobile-parts"
          :parts="video.parts"
          :selected="partId"
          @select="selectPart"
        />
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
          <button
            :disabled="busy"
            class="star-button"
            :aria-pressed="video.starred"
            :title="
              !session.user
                ? '登录后保存到我的星标'
                : video.starred
                  ? '从我的星标移除'
                  : '保存到我的星标'
            "
            @click="star"
          >
            <UiIcon name="star" />{{ busy ? '保存中…' : video.starred ? '已星标' : '星标' }}
          </button>
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
        <CommentsPanel :video-id="video.id" />
      </div>
      <aside class="watch-sidebar">
        <VideoUpCard :creators="video.creators" />
        <PartSelector
          v-if="video.parts && video.parts.length > 1"
          class="desktop-parts"
          :parts="video.parts"
          :selected="partId"
          @select="selectPart"
        />
        <RelatedVideos :video="video" />
      </aside>
    </div>
  </main>
</template>
