<script setup lang="ts">
import { computed } from 'vue';
import type { AudioCapability } from '../player/audioCapabilities';
import type { MeasuredMedia } from '../player/mediaInfo';
import { audioCompatibilityMessage, audioOnlyCopy } from '../player/audioAlternatives';
const props = defineProps<{
  media: MeasuredMedia;
  support: AudioCapability;
  alternativeId?: string;
  busy?: boolean;
}>();
defineEmits<{ variant: [value: string] }>();
const copy = computed(() => audioOnlyCopy(props.media));
const message = computed(() => audioCompatibilityMessage(props.media, props.support));
</script>
<template>
  <aside v-if="copy || message" class="audio-compatibility-notice" role="status">
    <p v-if="copy">
      当前使用 AAC 立体声兼容版：画面保留原视频流，声音经过转换，不含 Atmos。原档仍保留。
    </p>
    <template v-else>
      <p>{{ message }}</p>
      <template v-if="alternativeId">
        <p class="audio-alternative-help">
          已保存 AAC 立体声版，保留原视频画面；声音经过转换，不含 Atmos。
        </p>
        <button type="button" :disabled="busy" @click="$emit('variant', alternativeId)">
          使用 AAC 声音兼容版
        </button>
      </template>
      <p v-else class="audio-alternative-help">
        尚未保存声音兼容版，可请管理员生成；也可尝试支持 EC-3 的系统浏览器或播放器。原档不会被替换。
      </p>
    </template>
  </aside>
</template>
<style scoped>
.audio-compatibility-notice {
  margin: 12px 0;
  padding: 12px 14px;
  border: 1px solid var(--line, #dce3e2);
  border-radius: 10px;
  color: var(--text, #333);
  background: var(--surface, #fff);
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.audio-compatibility-notice p {
  margin: 0;
}
.audio-compatibility-notice p + p {
  margin-top: 5px;
}
.audio-alternative-help {
  opacity: 0.78;
}
.audio-compatibility-notice button {
  min-height: 44px;
  margin-top: 8px;
  padding: 8px 12px;
}
</style>
