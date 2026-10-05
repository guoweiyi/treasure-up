<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { ElAlert, ElButton, ElDialog, ElMessageBox } from 'element-plus';
import { api, errorText, session, write } from '../../api';
import type { Row } from '../../types';
import { finishedJob } from '../../utils/jobDisplay';
import { createJobPoller } from '../../utils/jobPoller';
import JobDetails from './JobDetails.vue';
const props = defineProps<{ open: boolean; job: Row | null }>();
const emit = defineEmits<{ 'update:open': [value: boolean]; completed: [job: Row] }>();
const current = ref<Row | null>(null),
  error = ref('');
const reimportBusy = ref(false),
  reimportAllowed = ref(false);
const mediaActionBusy = ref(false);
async function retryMedia(id: string, action: 'retry' | 'resume') {
  if (mediaActionBusy.value) return;
  const parentId = props.job?.id;
  mediaActionBusy.value = true;
  try {
    await write(`/admin/jobs/${encodeURIComponent(id)}/${action}`);
    if (props.open && props.job?.id === parentId) refresh();
  } catch (e) {
    if (props.open && props.job?.id === parentId) error.value = errorText(e);
  } finally {
    mediaActionBusy.value = false;
  }
}
async function allowReimport() {
  if (!current.value || reimportBusy.value) return;
  try {
    await ElMessageBox.confirm(
      '允许以后重新采集这条归档。已删除的文件不会恢复，停用的来源也不会自动开启。',
      '允许重新导入',
      { confirmButtonText: '允许', cancelButtonText: '返回' },
    );
  } catch {
    return;
  }
  reimportBusy.value = true;
  error.value = '';
  try {
    await write(`/admin/deletions/${encodeURIComponent(current.value.id)}/allow-reimport`);
    reimportAllowed.value = true;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    reimportBusy.value = false;
  }
}
const poller = createJobPoller(
  (id) => api<Row>(`/admin/jobs/${encodeURIComponent(id)}`),
  (job) => {
    current.value = job;
    if (job.result?.reimport_allowed) reimportAllowed.value = true;
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
    reimportAllowed.value = !!props.job?.result?.reimport_allowed;
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
    <JobDetails v-if="current" :job="current" @retry-media="retryMedia" />
    <div
      v-if="
        session.user?.role === 'admin' &&
        current?.status === 'succeeded' &&
        ['delete_video', 'delete_creator'].includes(current.kind)
      "
      class="deletion-reimport"
    >
      <p class="field-help">
        {{
          reimportAllowed
            ? '已允许重新导入，需要时可重新添加备份任务。'
            : '为避免内容再次出现，自动重新导入已关闭。'
        }}
      </p>
      <el-button v-if="!reimportAllowed" text :loading="reimportBusy" @click="allowReimport"
        >允许以后重新导入</el-button
      >
    </div>
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
