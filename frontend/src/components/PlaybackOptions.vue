<script setup lang="ts">
import { computed } from 'vue';
import type { Playback, Variant, MediaProperties } from '../types';
import { qualityLabel } from '../utils/format';
import PlayerChoice from '../player/PlayerChoice.vue';
import { rateChoices } from '../player/layout';
import { bitrate, type MeasuredMedia } from '../player/mediaInfo';
const props = defineProps<{
  playback: Playback | null;
  variants: Variant[];
  variantId: string;
  subtitle: string;
  routeId: string;
  balance: boolean;
  busy: boolean;
  rate: number;
  volume: number;
  fit: 'contain' | 'cover';
  mediaProperties?: Record<string, MediaProperties>;
}>();
defineEmits<{
  route: [value: string];
  balance: [value: boolean];
  variant: [value: string];
  subtitle: [value: string];
  rate: [value: number];
  volume: [value: number];
  fit: [value: 'contain' | 'cover'];
}>();
const balanceAvailable = computed(
  () => props.playback?.loudness?.status === 'ready' && !props.playback.loudness.atmos_bypass,
);
const balanceNote = computed(() => {
  const value = props.playback?.loudness;
  if (value?.atmos_bypass || value?.status === 'bypassed') return '空间或多声道音频保留原始音量';
  if (value?.status === 'silent') return '静音内容无需调整';
  if (value?.status === 'ready') return '降低过响内容的音量，不改变原档';
  return '此版本尚未完成音量分析';
});
const selectedRoute = computed(() =>
  props.playback?.routes?.find((route) => route.id === props.playback?.selected_route_id),
);
const selectedVariant = computed(() =>
  props.variants.find((variant) => variant.id === props.variantId),
);
const media = computed(
  () =>
    ({ ...props.mediaProperties?.[props.variantId], ...props.playback?.media }) as MeasuredMedia,
);
const variantChoices = computed(() =>
  props.variants.map((variant) => ({
    value: variant.id,
    label: qualityLabel(variant) + ' · ' + (variant.kind === 'playback' ? '兼容副本' : '原档'),
  })),
);
const subtitleChoices = computed(() => [
  { value: '', label: props.playback?.subtitles?.length ? '关闭' : '暂无已保存字幕' },
  ...(props.playback?.subtitles || []).map((track) => ({
    value: track.url,
    label: track.label + (track.is_auto ? '（自动）' : ''),
  })),
]);
const routeChoices = computed(() => [
  { value: '', label: '自动选择' },
  ...(props.playback?.routes || []).map((route) => ({
    value: route.id,
    label: route.name + (route.status === 'unavailable' ? '（不可用）' : ''),
    disabled: route.status === 'unavailable',
  })),
]);
</script>
<template>
  <div class="player-settings-fields">
    <PlayerChoice
      label="画质"
      :model-value="variantId"
      :choices="variantChoices"
      :disabled="busy || !variants.length"
      @update:model-value="$emit('variant', String($event))"
    />
    <PlayerChoice
      label="播放速度"
      :model-value="rate"
      :choices="rateChoices"
      @update:model-value="$emit('rate', Number($event))"
    />
    <PlayerChoice
      label="字幕"
      :model-value="subtitle"
      :choices="subtitleChoices"
      :disabled="!playback?.subtitles?.length"
      @update:model-value="$emit('subtitle', String($event))"
    />
    <PlayerChoice
      label="画面显示"
      :model-value="fit"
      :choices="[
        { value: 'contain', label: '完整显示' },
        { value: 'cover', label: '铺满画面（裁剪）' },
      ]"
      @update:model-value="$emit('fit', $event as 'contain' | 'cover')"
    />
    <PlayerChoice
      label="播放节点"
      :model-value="routeId"
      :choices="routeChoices"
      :disabled="busy"
      @update:model-value="$emit('route', String($event))"
    />
    <p v-if="!routeId && selectedRoute" class="player-setting-help">
      当前：{{ selectedRoute.name }}
    </p>
    <div class="player-setting-grid">
      <label
        >音量 <output>{{ Math.round(volume * 100) }}%</output
        ><input
          :value="Math.round(volume * 100)"
          type="range"
          aria-label="音量"
          min="0"
          max="100"
          @input="$emit('volume', Number(($event.target as HTMLInputElement).value) / 100)"
      /></label>
    </div>
    <label class="player-switch"
      ><span
        >音量平衡<small>{{ balanceNote }}</small></span
      ><input
        type="checkbox"
        :checked="balance && balanceAvailable"
        :disabled="!balanceAvailable || busy"
        @change="$emit('balance', ($event.target as HTMLInputElement).checked)"
    /></label>
    <p v-if="media?.compatibility === 'hdr_conversion_unsupported'" class="player-setting-help">
      原档已保存，暂不支持生成 HDR 浏览器兼容副本。
    </p>
    <details class="player-settings-details">
      <summary>媒体信息</summary>
      <dl class="player-media-details">
        <dt>版本</dt>
        <dd>{{ selectedVariant?.kind === 'playback' ? '兼容副本' : '原档' }}</dd>
        <dt>视频</dt>
        <dd>
          {{ selectedVariant?.video_codec || '未记录'
          }}{{ media?.dolby_vision ? ' · 杜比视界' : media?.hdr ? ' · HDR' : '' }}
        </dd>
        <dt>音频</dt>
        <dd>
          {{ selectedVariant?.audio_codec || '未记录'
          }}{{ media?.dolby_atmos ? ' · 杜比全景声' : '' }}
        </dd>
        <dt>码率</dt>
        <dd>
          {{ bitrate(media?.total_bitrate_bps)
          }}<span v-if="media?.video_bitrate_bps">
            · 视频 {{ bitrate(media.video_bitrate_bps) }}</span
          ><span v-if="media?.audio_bitrate_bps">
            · 音频 {{ bitrate(media.audio_bitrate_bps) }}</span
          >
        </dd>
        <dt>传输</dt>
        <dd>{{ playback?.protocol === 'hls' ? '原码流分片' : '文件直读' }}</dd>
      </dl>
      <p class="player-setting-help">
        格式支持取决于浏览器和设备。播放失败时可手动选择已保存的兼容副本。
      </p>
    </details>
  </div>
</template>
