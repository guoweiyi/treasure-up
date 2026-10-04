<script setup lang="ts">
import { computed } from 'vue';
import { bytes, date, statusText } from '../../api';
import type { Row } from '../../types';
import { jobCounts, jobNames, jobPhase } from '../../utils/jobDisplay';
const props = defineProps<{ job: Row }>();
const counts = computed(() => jobCounts(props.job));
const transfer = computed(() => props.job.result?.progress || {});
const progress = computed(() => props.job.checkpoint?.progress || {});
const updatedAt = computed(
  () =>
    [progress.value.updated_at, transfer.value.updated_at]
      .filter((value) => typeof value === 'string' && Number.isFinite(Date.parse(value)))
      .sort((left, right) => Date.parse(right) - Date.parse(left))[0],
);
const checkNames: Record<string, string> = {
  put: '写入对象',
  head: '读取对象信息',
  sha256_readback: '完整读取与 SHA-256 校验',
  single_range: '视频范围读取',
};
</script>
<template>
  <section class="job-details">
    <h3>{{ jobNames[job.kind] || '后台任务' }} · {{ statusText(job.status) }}</h3>
    <p v-if="job.target_title">
      <strong>{{ job.target_title }}</strong>
    </p>
    <p>{{ jobPhase(job) }}</p>
    <dl v-if="counts.length" class="job-metrics">
      <template v-for="[label, value] in counts" :key="String(label)"
        ><dt>{{ label }}</dt>
        <dd>{{ value }}</dd></template
      >
    </dl>
    <p v-if="Number.isFinite(transfer.downloaded_bytes)">
      {{ transfer.scope === 'current_stream' ? '当前音视频流已下载' : '已下载' }}
      {{ bytes(transfer.downloaded_bytes)
      }}<template v-if="Number.isFinite(transfer.total_bytes)">
        / 约 {{ bytes(transfer.total_bytes) }}</template
      ><template v-if="transfer.speed_bytes_per_second > 0">
        · {{ bytes(transfer.speed_bytes_per_second) }}/秒</template
      ><template v-if="Number.isFinite(transfer.eta_seconds)">
        · 预计剩余 {{ Math.ceil(transfer.eta_seconds) }} 秒</template
      >
    </p>
    <div v-if="job.kind === 'probe_storage' && job.result?.status" class="probe-results">
      <p>
        <strong>{{
          job.result.status === 'passed' ? '存储读写探测通过' : '存储探测未通过'
        }}</strong>
      </p>
      <dl class="job-metrics">
        <template v-for="(label, key) in checkNames" :key="key"
          ><dt>{{ label }}</dt>
          <dd>
            {{
              job.result.checks?.[key] === true
                ? '通过'
                : job.result.checks?.[key] === false
                  ? '失败'
                  : '未测试'
            }}
          </dd></template
        >
      </dl>
      <p class="muted small">
        分片上传：{{ job.result.multipart === 'passed' ? '通过' : '未测试' }} · 浏览器跨域：{{
          job.result.browser_cors === 'passed' ? '通过' : '未测试'
        }}
      </p>
      <p class="muted small">此结果只代表列出的实际检查；不能据此确认未测试的云端能力。</p>
    </div>
    <p v-if="job.kind === 'verify_account' && job.result?.logged_in === true">
      B 站登录有效{{ job.result.vip ? ' · 会员状态有效' : '' }}
    </p>
    <p v-if="job.result?.old_locations_retained || job.result?.originals_retained">
      原存储位置的文件保留。
    </p>
    <p v-if="job.error" class="form-error" role="alert">{{ job.error }}</p>
    <dl class="job-metrics muted small">
      <dt>创建时间</dt>
      <dd>{{ date(job.created_at) }}</dd>
      <dt>开始时间</dt>
      <dd>{{ date(job.started_at) }}</dd>
      <template v-if="updatedAt"
        ><dt>最近进度</dt>
        <dd>{{ date(updatedAt) }}</dd></template
      >
      <template v-if="job.finished_at"
        ><dt>结束时间</dt>
        <dd>{{ date(job.finished_at) }}</dd></template
      >
      <template v-if="job.available_at && job.status === 'queued'"
        ><dt>可执行时间</dt>
        <dd>{{ date(job.available_at) }}</dd></template
      >
      <dt>已尝试</dt>
      <dd>{{ job.attempts ?? 0 }} / {{ job.max_attempts ?? '—' }}</dd>
    </dl>
    <details class="small muted">
      <summary>任务定位信息</summary>
      <p>任务 ID：{{ job.id }}</p>
    </details>
  </section>
</template>
<style scoped>
.job-details h3 {
  margin: 0 0 10px;
}
.job-metrics {
  display: grid;
  grid-template-columns: minmax(90px, auto) 1fr;
  gap: 7px 18px;
}
.job-metrics dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.job-details details {
  margin-top: 16px;
}
.job-details details p {
  overflow-wrap: anywhere;
}
.probe-results {
  padding: 12px;
  border-radius: 10px;
  background: var(--surface-muted, #f0f3f7);
}
</style>
