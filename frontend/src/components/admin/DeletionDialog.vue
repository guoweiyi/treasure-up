<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { ElAlert, ElButton, ElDialog } from 'element-plus';
import { api, ApiError, bytes, errorText, write } from '../../api';
import type { Row } from '../../types';

const props = defineProps<{ target: { kind: 'videos' | 'creators'; id: string } | null }>();
const emit = defineEmits<{ close: []; started: [job: Row] }>();
const preview = ref<Row | null>(null);
const loading = ref(false);
const submitting = ref(false);
const error = ref('');
let controller: AbortController | undefined;
let revision = 0;

async function loadPreview() {
  controller?.abort();
  const ticket = ++revision;
  preview.value = null;
  if (!props.target) return;
  controller = new AbortController();
  loading.value = true;
  try {
    const result = await api<Row>(
      `/admin/${props.target.kind}/${encodeURIComponent(props.target.id)}/deletion-preview`,
      { signal: controller.signal },
    );
    if (ticket === revision) preview.value = result;
  } catch (e) {
    if (ticket === revision && !controller?.signal.aborted) error.value = errorText(e);
  } finally {
    if (ticket === revision) loading.value = false;
  }
}
watch(
  () => props.target,
  () => {
    error.value = '';
    void loadPreview();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  revision++;
  controller?.abort();
});

async function remove() {
  if (!props.target || !preview.value || submitting.value) return;
  const target = { ...props.target };
  const label = preview.value.label;
  submitting.value = true;
  error.value = '';
  try {
    const result = await write<Row>(
      `/admin/${target.kind}/${encodeURIComponent(target.id)}/deletion`,
      {
        confirm: true,
        preview_token: preview.value.preview_token,
      },
    );
    // The deletion is accepted even if the next progress request temporarily fails.
    emit('started', {
      id: result.job_id,
      kind: target.kind === 'videos' ? 'delete_video' : 'delete_creator',
      status: result.status,
      target_title: label,
    });
    emit('close');
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      error.value = '内容状态已变化，请核对更新后的范围再确认。';
      await loadPreview();
    } else error.value = errorText(e);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="!!target"
    :title="target?.kind === 'creators' ? '删除 UP 主与归档' : '删除视频归档'"
    width="min(540px, 94vw)"
    :close-on-click-modal="!submitting"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    @update:model-value="!$event && !submitting && emit('close')"
  >
    <div v-if="loading" class="deletion-loading">正在核对关联内容…</div>
    <template v-else-if="preview">
      <p class="deletion-title">{{ preview.label }}</p>
      <div class="deletion-counts">
        <span
          ><strong>{{ preview.video_count }}</strong> 视频</span
        >
        <span
          ><strong>{{ preview.comment_count }}</strong> 评论</span
        >
        <span
          ><strong>{{ bytes(preview.asset_bytes) }}</strong> 关联文件</span
        >
      </div>
      <p v-if="target?.kind === 'creators'">
        将删除此 UP 主的归档资料<span v-if="preview.video_count"
          >及其发布的 {{ preview.video_count }} 条视频、弹幕和评论</span
        >，并清理不再使用的文件。此操作无法撤销。
      </p>
      <p v-else>将删除本站的视频、弹幕、评论和关联资料，并清理不再使用的文件。此操作无法撤销。</p>
      <ul class="deletion-notes">
        <li v-if="preview.source_count">
          停用 {{ preview.source_count }} 个关联备份来源，防止内容再次自动导入。
        </li>
        <li v-if="preview.active_job_count">
          停止 {{ preview.active_job_count }} 个相关任务后开始清理。
        </li>
        <li v-if="preview.collaboration_count">
          保留 {{ preview.collaboration_count }} 条其他 UP 主的联合投稿，仅移除此 UP 主的署名关联。
        </li>
        <li v-if="preview.shared_asset_count">
          其他内容仍在使用的 {{ preview.shared_asset_count }} 个文件会保留。
        </li>
        <li v-if="preview.backup_protected_asset_count">
          已有备份引用的 {{ preview.backup_protected_asset_count }} 个文件会保留。
        </li>
      </ul>
      <p class="deletion-scope">仅清理本站归档，不影响 B 站上的视频与收藏。</p>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="mt" />
    <template #footer>
      <el-button :disabled="submitting" @click="emit('close')">保留</el-button>
      <el-button v-if="!preview && !loading" @click="loadPreview">重新核对</el-button>
      <el-button type="danger" :loading="submitting" :disabled="!preview || loading" @click="remove"
        >确认删除</el-button
      >
    </template>
  </el-dialog>
</template>

<style scoped>
.deletion-title {
  font-size: 16px;
  font-weight: 600;
  color: #262b2a;
  overflow-wrap: anywhere;
}
.deletion-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  padding: 16px 0;
  border-block: 1px solid var(--border);
  margin-bottom: 20px;
  font-size: 12px;
  color: #727b79;
}
.deletion-counts strong {
  color: #343c39;
  font-size: 16px;
  margin-right: 4px;
  font-variant-numeric: tabular-nums;
}
.deletion-notes {
  padding-left: 20px;
  line-height: 1.9;
  font-size: 13px;
}
.deletion-scope,
.deletion-loading {
  color: #7c8582;
  font-size: 12px;
}
.deletion-loading {
  padding: 30px 0;
  text-align: center;
}
</style>
