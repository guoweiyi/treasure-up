<script setup lang="ts">
import { computed } from 'vue';
import type { Playback, Variant, MediaProperties } from '../types';
import { qualityLabel } from '../utils/format';
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
const media = computed(() => props.playback?.media || props.mediaProperties?.[props.variantId]);
</script>
<template>
  <div class="player-settings-fields">
    <div class="player-setting-grid">
      <label
        >画质<select
          :value="variantId"
          :disabled="busy || !variants.length"
          @change="$emit('variant', ($event.target as HTMLSelectElement).value)"
        >
          <option v-for="variant in variants" :key="variant.id" :value="variant.id">
            {{ qualityLabel(variant) }} · {{ variant.kind === 'playback' ? '兼容副本' : '原档' }}
          </option>
        </select></label
      >
      <label
        >播放速度<select
          :value="rate"
          @change="$emit('rate', Number(($event.target as HTMLSelectElement).value))"
        >
          <option
            v-for="value in [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 3]"
            :key="value"
            :value="value"
          >
            {{ value }} 倍{{ value === 1 ? '（正常）' : '' }}
          </option>
        </select></label
      >
      <label
        >字幕<select
          :value="subtitle"
          :disabled="!playback?.subtitles?.length"
          @change="$emit('subtitle', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">{{ playback?.subtitles?.length ? '关闭' : '暂无已保存字幕' }}</option>
          <option v-for="track in playback?.subtitles" :key="track.url" :value="track.url">
            {{ track.label }}{{ track.is_auto ? '（自动）' : '' }}
          </option>
        </select></label
      >
      <label
        >画面显示<select
          :value="fit"
          @change="$emit('fit', ($event.target as HTMLSelectElement).value as 'contain' | 'cover')"
        >
          <option value="contain">完整显示</option>
          <option value="cover">铺满（裁剪边缘）</option>
        </select></label
      >
      <label
        >播放节点<select
          :value="routeId"
          :disabled="busy"
          @change="$emit('route', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">自动选择</option>
          <option
            v-for="route in playback?.routes"
            :key="route.id"
            :value="route.id"
            :disabled="route.status === 'unavailable'"
          >
            {{ route.name }}{{ route.status === 'unavailable' ? '（不可用）' : '' }}
          </option></select
        ><small v-if="!routeId && selectedRoute">当前：{{ selectedRoute.name }}</small></label
      >
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
        <dt>传输</dt>
        <dd>{{ playback?.protocol === 'hls' ? '原码流分片' : '文件直读' }}</dd>
      </dl>
      <p class="player-setting-help">
        格式支持取决于浏览器和设备。播放失败时可手动选择已保存的兼容副本。
      </p>
    </details>
  </div>
</template>
