<script setup lang="ts">
import { date } from '../api';
import { monitorStatus, type SourceMonitorState } from './source-monitor';
defineProps<{
  monitor?: SourceMonitorState;
  lastScanAt?: string | null;
  creator?: boolean;
  enabled?: boolean;
}>();
</script>
<template>
  <div class="source-public-status">
    <span>{{
      enabled === false ? '自动备份已暂停' : monitorStatus[monitor?.status || ''] || '尚未检查更新'
    }}</span>
    <span v-if="monitor?.last_completed_at || lastScanAt"
      >上次检查 {{ date(monitor?.last_completed_at || lastScanAt || undefined) }}</span
    >
    <span
      v-if="
        monitor?.counts &&
        typeof monitor.counts[creator ? 'newly_published' : 'newly_favorited'] === 'number'
      "
      >本轮{{ creator ? '新投稿' : '新收藏' }}
      {{ monitor.counts[creator ? 'newly_published' : 'newly_favorited'] }} 条</span
    >
  </div>
</template>
