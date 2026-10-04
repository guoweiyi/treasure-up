<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { ElAlert, ElButton, ElDialog } from 'element-plus';
import { api, errorText } from '../../api';
import type { Row } from '../../types';
import { finishedJob } from '../../utils/jobDisplay';
import { createJobPoller } from '../../utils/jobPoller';
import JobDetails from './JobDetails.vue';
const props = defineProps<{ open: boolean; job: Row | null }>();
const emit = defineEmits<{ 'update:open': [value: boolean]; completed: [job: Row] }>();
const current = ref<Row | null>(null),
  error = ref('');
const poller = createJobPoller(
  (id) => api<Row>(`/admin/jobs/${encodeURIComponent(id)}`),
  (job) => {
    current.value = job;
    error.value = '';
    if (finishedJob(job)) emit('completed', job);
  },
  (e) => {
    error.value = errorText(e);
  },
  finishedJob,
);
function refresh() {
  if (props.job?.id) {
    error.value = '';
    poller.start(props.job.id);
  }
}
watch(
  [() => props.open, () => props.job?.id],
  () => {
    poller.stop();
    error.value = '';
    current.value = props.job;
    if (props.open && props.job?.id) refresh();
  },
  { immediate: true },
);
onBeforeUnmount(poller.stop);
</script>
<template>
  <el-dialog
    :model-value="open"
    title="操作进度与结果"
    width="min(620px, 94vw)"
    @update:model-value="emit('update:open', $event)"
  >
    <JobDetails v-if="current" :job="current" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mt" />
    <p v-if="current && !finishedJob(current)" class="field-help">
      进度自动更新。关闭此窗口不会取消后台任务，可在任务中心继续查看。
    </p>
    <template #footer
      ><el-button @click="refresh">刷新状态</el-button
      ><el-button type="primary" @click="emit('update:open', false)">关闭</el-button></template
    >
  </el-dialog>
</template>
