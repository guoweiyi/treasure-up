<script setup lang="ts">
import { computed } from 'vue';
const props = defineProps<{
  setup?: { account_ready: boolean; storage_ready: boolean; source_ready: boolean };
}>();
const steps = computed(() => [
  {
    done: props.setup?.account_ready,
    title: '连接 B 站账号',
    text: '扫码授权或填写 Cookie，用于访问你有权限保存的内容。',
    to: '/admin/accounts',
  },
  {
    done: props.setup?.storage_ready,
    title: '确认保存位置',
    text: '本地磁盘开箱即用，也可添加对象存储。',
    to: '/admin/storage',
  },
  {
    done: props.setup?.source_ready,
    title: '选择首个来源',
    text: '选择收藏夹或 UP 主，设置自动检查新视频。',
    to: '/admin/sources',
  },
]);
</script>
<template>
  <section v-if="setup && steps.some((step) => !step.done)" class="setup-checklist">
    <header>
      <h2>开始保存你的视频</h2>
      <span>{{ steps.filter((step) => step.done).length }} / 3 已就绪</span>
    </header>
    <div class="setup-steps">
      <RouterLink
        v-for="(step, index) in steps"
        :key="step.to"
        :to="step.to"
        :class="{ done: step.done }"
        ><span class="setup-number" aria-hidden="true">{{ step.done ? '✓' : index + 1 }}</span
        ><span
          ><strong>{{ step.title }}<small v-if="step.done">已就绪</small></strong>
          <p>{{ step.text }}</p></span
        ><span aria-hidden="true" class="setup-arrow">→</span></RouterLink
      >
    </div>
  </section>
</template>
<style scoped>
.setup-checklist {
  padding: 22px;
  border: 1px solid #e1e5e2;
  border-radius: 7px;
  margin: 0 0 22px;
  background: #fff;
}
.setup-checklist header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 14px;
}
.setup-checklist h2 {
  margin: 0;
  font-size: 17px;
}
.setup-checklist header > span {
  color: #8c9690;
  font-size: 12px;
}
.setup-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
.setup-steps a {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 0 0;
  color: #454e49;
  border-top: 1px solid #eef0ed;
}
.setup-number {
  display: grid;
  place-items: center;
  width: 23px;
  height: 23px;
  flex-shrink: 0;
  background: #edf1ee;
  color: #5d7163;
  border-radius: 50%;
  font-size: 11px;
}
.setup-steps strong {
  font-size: 13px;
  font-weight: 500;
}
.setup-steps small {
  color: #98a09b;
  font-size: 10px;
  margin-left: 8px;
  font-weight: 400;
}
.setup-steps p {
  color: #949b96;
  font-size: 12px;
  line-height: 1.65;
  margin: 6px 0 0;
}
.setup-arrow {
  margin-left: auto;
  font-size: 14px;
  color: #8c9690;
}
.setup-steps a:hover strong {
  color: var(--accent);
}
@media (max-width: 1000px) {
  .setup-steps {
    grid-template-columns: 1fr;
    gap: 10px;
  }
}
</style>
