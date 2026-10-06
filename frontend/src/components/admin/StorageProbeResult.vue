<script setup lang="ts">
import { ElDialog, ElButton } from 'element-plus';
import type { Row } from '../../types';
import { date } from '../../api';
defineProps<{ open: boolean; result: Row | null; name: string }>();
defineEmits<{ 'update:open': [value: boolean] }>();
const labels: Record<string, string> = {
  put: '写入测试文件',
  head: '读取文件信息',
  sha256_readback: '下载并校验内容',
  single_range: '按范围读取',
  delete: '清理测试文件',
};
</script>
<template>
  <el-dialog
    :model-value="open"
    title="存储连接测试"
    width="min(520px, 94vw)"
    @update:model-value="$emit('update:open', $event)"
  >
    <template v-if="result">
      <div class="probe-heading" :class="{ failed: result.status !== 'passed' }">
        <span class="probe-symbol">{{ result.status === 'passed' ? '✓' : '!' }}</span>
        <div>
          <h3>{{ result.status === 'passed' ? '连接正常' : '连接测试未通过' }}</h3>
          <p>
            {{ name
            }}<template v-if="result.elapsed_ms != null">
              · {{ (result.elapsed_ms / 1000).toFixed(2) }} 秒</template
            >
          </p>
        </div>
      </div>
      <p v-if="result.message" class="probe-message">{{ result.message }}</p>
      <dl class="probe-checks">
        <template v-for="(label, key) in labels" :key="key"
          ><dt>{{ label }}</dt>
          <dd
            :class="{
              passed: result.checks?.[key] === true,
              failed: result.checks?.[key] === false,
            }"
          >
            {{
              result.checks?.[key] === true
                ? '通过'
                : result.checks?.[key] === false
                  ? '未通过'
                  : '未执行'
            }}
          </dd></template
        >
        <dt>直连跨域与范围响应</dt>
        <dd
          :class="{
            passed: result.browser_cors === 'passed',
            failed: result.browser_cors === 'failed',
          }"
        >
          {{
            result.browser_cors === 'passed'
              ? '通过'
              : result.browser_cors === 'failed'
                ? '未通过'
                : '未测试'
          }}
        </dd>
      </dl>
      <div v-if="result.browser_cors === 'failed'" class="probe-warning" role="alert">
        <strong>存储可连接，浏览器直连尚未就绪</strong>
        <p>
          {{
            result.browser_message ||
            '请在存储服务或 CDN 配置中允许本站跨域读取，并确认支持按范围请求。分片播放可能因此无法加载。'
          }}
        </p>
      </div>
      <p v-else-if="result.browser_message" class="field-help">{{ result.browser_message }}</p>
      <p v-if="result.browser_cors && result.browser_cors !== 'not_tested'" class="field-help">
        由服务器携带本站来源检查外网文件地址的响应头；实际播放仍以使用设备的浏览器为准。
      </p>
      <p class="field-help">分片上传不在本次连接测试范围内。</p>
      <p v-if="result.verified_at" class="field-help">{{ date(result.verified_at) }}</p>
    </template>
    <template #footer
      ><el-button type="primary" @click="$emit('update:open', false)">完成</el-button></template
    >
  </el-dialog>
</template>
<style scoped>
.probe-heading {
  display: flex;
  gap: 12px;
  align-items: center;
  color: #317257;
}
.probe-symbol {
  font-size: 28px;
}
h3 {
  margin: 0 0 3px;
  font-size: 17px;
  font-weight: 600;
}
p {
  margin: 0;
  color: var(--muted);
}
.probe-message {
  margin-top: 16px;
  color: var(--text);
  overflow-wrap: anywhere;
}
.probe-checks {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  margin: 22px 0;
}
dd {
  margin: 0;
  color: var(--muted);
}
.passed {
  color: #317257;
}
.failed {
  color: #ae413d;
}
.probe-warning {
  padding: 12px 14px;
  margin: 16px 0;
  border-left: 3px solid #bd8332;
  background: #fbf5e9;
  color: #785318;
}
.probe-warning strong {
  font-weight: 600;
}
.probe-warning p {
  margin-top: 5px;
  color: inherit;
  overflow-wrap: anywhere;
}
.field-help {
  margin-top: 8px;
}
</style>
