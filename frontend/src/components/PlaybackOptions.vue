<script setup lang="ts">
import { computed } from 'vue';
import type { Playback } from '../types';
import { bytes } from '../api';
const props = defineProps<{
  playback: Playback | null;
  routeId: string;
  balance: boolean;
  busy: boolean;
}>();
defineEmits<{ route: [value: string]; balance: [value: boolean] }>();
const selected = computed(() =>
  props.playback?.routes?.find((route) => route.id === props.playback?.selected_route_id),
);
const balanceAvailable = computed(
  () => props.playback?.loudness?.status === 'ready' && !props.playback.loudness.atmos_bypass,
);
const balanceNote = computed(() => {
  const value = props.playback?.loudness;
  if (value?.atmos_bypass || value?.status === 'bypassed')
    return '空间音频或多声道音频绕过音量平衡';
  if (value?.status === 'silent') return '静音内容无需平衡';
  if (value?.status === 'ready')
    return `播放时衰减 ${Math.abs(value.gain_db).toFixed(1)} dB，原档保持不变`;
  return '此版本尚无音量分析';
});
</script>
<template>
  <div v-if="playback" class="playback-options">
    <div class="playback-option-line">
      <span class="delivery-mode">{{
        playback.protocol === 'hls' ? '原码流分片' : '文件直读'
      }}</span
      ><label v-if="playback.routes?.length"
        >播放节点<select
          :value="routeId"
          :disabled="busy"
          @change="$emit('route', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">自动选择</option>
          <option
            v-for="route in playback.routes"
            :key="route.id"
            :value="route.id"
            :disabled="route.status === 'unavailable'"
          >
            {{ route.name }}{{ route.status === 'unavailable' ? '（不可用）' : '' }}
          </option>
        </select></label
      ><label class="check-label" :title="balanceNote"
        ><input
          type="checkbox"
          :checked="balance && balanceAvailable"
          :disabled="!balanceAvailable || busy"
          @change="$emit('balance', ($event.target as HTMLInputElement).checked)"
        />音量平衡</label
      >
    </div>
    <p class="playback-route-note">
      <span v-if="selected"
        >当前节点：{{ selected.name
        }}<template v-if="selected.latency_ms != null">
          · {{ Math.round(selected.latency_ms) }} ms</template
        ><template v-if="selected.throughput_bps != null">
          · {{ bytes(selected.throughput_bps) }}/s</template
        >
        ·
        {{
          selected.measurement_scope === 'browser_delivery'
            ? '本浏览器测量'
            : selected.measurement_scope === 'server_storage'
              ? '服务端参考'
              : '尚无测速'
        }}</span
      ><span>{{ balanceNote }}</span>
    </p>
  </div>
</template>
