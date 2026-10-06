<script setup lang="ts">
import { computed } from 'vue';
import type { Playback, Variant, MediaProperties } from '../types';
import { qualityLabel } from '../utils/format';
import PlayerChoice from '../player/PlayerChoice.vue';
import { rateChoices } from '../player/layout';
import type { RuntimeStats } from '../player/runtimeStats';
import { bitrate, type MeasuredMedia } from '../player/mediaInfo';
import { audioVariantSuffix, sameOriginalAudioAlternative } from '../player/audioAlternatives';
import AudioCompatibilityNotice from './AudioCompatibilityNotice.vue';
const props = defineProps<{
  playback: Playback | null;
  runtimeStats: RuntimeStats;
  variants: Variant[];
  variantId: string;
  subtitle: string;
  routeId: string;
  protocol: 'auto' | 'file';
  balance: boolean;
  busy: boolean;
  rate: number;
  volume: number;
  fit: 'contain' | 'cover';
  mediaProperties?: Record<string, MediaProperties>;
}>();
defineEmits<{
  route: [value: string];
  protocol: [value: 'auto' | 'file'];
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
    ({
      audio_codec: selectedVariant.value?.audio_codec,
      ...selectedVariant.value?.metadata,
      ...props.mediaProperties?.[props.variantId],
      ...props.playback?.media,
    }) as MeasuredMedia,
);
const audioAlternative = computed(() =>
  sameOriginalAudioAlternative(props.variantId, props.variants, props.mediaProperties, media.value),
);
const variantChoices = computed(() =>
  props.variants.map((variant) => ({
    value: variant.id,
    label: qualityLabel(variant) + ' · ' + audioVariantSuffix(variant, props.mediaProperties),
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
    <AudioCompatibilityNotice
      :media="media"
      :support="runtimeStats.audioSupport.ec3"
      :alternative-id="audioAlternative?.id"
      :busy="busy"
      @variant="$emit('variant', $event)"
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
    <PlayerChoice
      label="播放方式"
      :model-value="protocol"
      :choices="[
        { value: 'auto', label: '自动选择' },
        { value: 'file', label: '文件直读' },
      ]"
      :disabled="busy"
      @update:model-value="$emit('protocol', $event as 'auto' | 'file')"
    />
    <p class="player-setting-help">声音异常时可切换文件直读对照，画质和音轨保持不变。</p>
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
      <summary>播放统计与媒体详情</summary>
      <dl class="player-media-details">
        <dt>播放引擎</dt>
        <dd>{{ runtimeStats.engine }}</dd>
        <dt>MIME</dt>
        <dd>{{ runtimeStats.mime || media.mime_type || '—' }}</dd>
        <dt>codecs</dt>
        <dd>
          {{
            runtimeStats.codecs ||
            [
              media.video_codec || selectedVariant?.video_codec,
              media.audio_codec || selectedVariant?.audio_codec,
            ]
              .filter(Boolean)
              .join(', ') ||
            '—'
          }}<small>{{ runtimeStats.codecs ? ' · 当前 MSE 缓冲区' : ' · 归档探测' }}</small>
        </dd>
        <dt>画面</dt>
        <dd>
          {{
            runtimeStats.width && runtimeStats.height
              ? `${runtimeStats.width} × ${runtimeStats.height}`
              : '—'
          }}
          · {{ media.fps ? `${Number(media.fps.toFixed(2))} fps（文件）` : '— fps' }}
        </dd>
        <dt>视频码率</dt>
        <dd>
          {{ media.video_bitrate_bps ? bitrate(media.video_bitrate_bps) : '—' }}
          <small>文件平均值</small>
        </dd>
        <dt>音频码率</dt>
        <dd>
          {{ media.audio_bitrate_bps ? bitrate(media.audio_bitrate_bps) : '—' }}
          <small>文件平均值</small>
        </dd>
        <dt>已加载分片</dt>
        <dd>
          {{ runtimeStats.loadedFragments ?? '—'
          }}<template v-if="media.segment_count != null"> / {{ media.segment_count }}</template
          ><small>{{
            runtimeStats.loadedFragments == null ? ' · 当前引擎未提供' : ' · 当前会话去重计数'
          }}</small>
        </dd>
        <dt>丢帧 / 总帧</dt>
        <dd>{{ runtimeStats.droppedFrames ?? '—' }} / {{ runtimeStats.totalFrames ?? '—' }}</dd>
        <dt>{{ runtimeStats.hostIsFinal ? '分发 host' : '请求 host' }}</dt>
        <dd>
          {{ runtimeStats.host || '—' }}
          <small>{{
            runtimeStats.hostIsFinal ? '最终媒体响应节点' : '请求入口，最终节点不可见'
          }}</small>
        </dd>
        <dt>网络采样</dt>
        <dd>
          {{ runtimeStats.networkBps ? bitrate(runtimeStats.networkBps) : '—'
          }}<small v-if="runtimeStats.networkBps"> · 最近一次媒体响应体传输</small>
        </dd>
        <dt>归档特性</dt>
        <dd>
          {{
            [
              media.dolby_vision ? 'Dolby Vision' : media.hdr ? 'HDR' : '',
              media.dolby_atmos ? 'Dolby Atmos' : '',
            ]
              .filter(Boolean)
              .join(' · ') || '—'
          }}
        </dd>
        <template v-if="['eac3', 'ec-3'].includes(media.audio_codec || '')">
          <dt>EC-3 解码</dt>
          <dd>
            {{
              runtimeStats.audioSupport.ec3 === 'supported'
                ? '当前播放引擎报告支持（非实际声音输出证明）'
                : runtimeStats.audioSupport.ec3 === 'unsupported'
                  ? '当前播放引擎报告不支持'
                  : '未提供能力信息'
            }}
          </dd>
          <dt v-if="media.dolby_atmos">Atmos 信令</dt>
          <dd v-if="media.dolby_atmos">
            {{
              media.ec3?.joc
                ? `JOC · complexity ${media.ec3.complexity_index_type_a}`
                : '原码流已检测，封装信令未记录'
            }}<small v-if="media.ec3_configuration_verified"> · HLS 初始化已核对</small>
          </dd>
          <dt v-if="media.dolby_atmos">空间音频输出</dt>
          <dd v-if="media.dolby_atmos">
            {{
              runtimeStats.audioSupport.spatial === 'supported'
                ? '设备报告支持；实际输出未验证'
                : runtimeStats.audioSupport.spatial === 'unsupported'
                  ? '当前输出报告不支持空间呈现；不等于不能播放普通声音'
                  : '未验证，取决于设备与音频输出路径'
            }}
          </dd>
        </template>
      </dl>
      <p class="player-setting-help">
        网络采样来自本站播放请求的浏览器计时，15 秒后过期；缓存、原生分片或跨域计时不可见时显示
        —。文件平均码率不代表实时网速。格式支持取决于浏览器和设备。
      </p>
    </details>
  </div>
</template>
