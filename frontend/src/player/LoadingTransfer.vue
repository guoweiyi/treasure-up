<script setup lang="ts">
import { computed } from 'vue';
import { emptyTransferSnapshot, formatTransferRate, type TransferSnapshot } from './transferMeter';
const props = withDefaults(
  defineProps<{
    phase?: 'preparing' | 'switching' | 'buffering';
    transfer?: TransferSnapshot | null;
  }>(),
  { phase: 'buffering', transfer: null },
);
const current = computed(() => props.transfer || emptyTransferSnapshot());
const label = computed(
  () =>
    ({ preparing: '正在准备播放', switching: '正在切换播放', buffering: '正在缓冲' })[props.phase],
);
const note = computed(() => {
  if (props.phase === 'preparing') return '';
  if (!current.value.supported) return '当前播放方式未提供实时速度';
  if (current.value.state === 'stalled') return '等待媒体数据';
  if (current.value.state === 'waiting') return '正在连接';
  if (current.value.state === 'idle') return '等待媒体请求';
  return '';
});
</script>
<template>
  <div class="player-loading loading-transfer">
    <span class="transfer-mark" aria-hidden="true"></span>
    <div class="transfer-content">
      <div class="transfer-line">
        <span role="status">{{ label }}</span
        ><output aria-label="媒体下载速度" aria-live="off">{{
          formatTransferRate(current.bytesPerSecond)
        }}</output>
      </div>
      <small v-if="note">{{ note }}</small>
    </div>
  </div>
</template>
<style scoped>
.loading-transfer {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 164px;
  max-width: calc(100% - 24px);
  font-variant-numeric: tabular-nums;
}
.transfer-mark {
  flex: 0 0 13px;
  width: 13px;
  height: 13px;
  border: 1.5px solid #ffffff45;
  border-top-color: #fff;
  border-radius: 50%;
  animation: transfer-spin 0.9s linear infinite;
}
.transfer-content {
  min-width: 0;
  flex: 1;
}
.transfer-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}
output {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
small {
  display: block;
  margin-top: 3px;
  opacity: 0.72;
  font-size: 10px;
  line-height: 1.35;
}
@keyframes transfer-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .transfer-mark {
    animation: none;
  }
}
</style>
