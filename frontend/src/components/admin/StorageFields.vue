<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElOption,
  ElOptionGroup,
  ElSelect,
} from 'element-plus';
import { api, errorText } from '../../api';
import {
  changeStorageKind,
  graphStorage,
  s3Storage,
  storageCredentialFields,
  storageDiscoveryPayload,
  storageProviders,
  type StorageDraft,
  type StorageKind,
} from '../../utils/storageForm';
const props = defineProps<{ value: StorageDraft }>();
const graph = computed(() => graphStorage(props.value.kind));
const fields = computed(() => storageCredentialFields(props.value.kind));
const discovering = ref(false),
  discoveryMessage = ref(''),
  discoveryError = ref('');
type Choice = { name: string; id?: string; region?: string; endpoint?: string };
const choices = ref<Choice[]>([]);
let sequence = 0,
  controller: AbortController | undefined;
function clearDiscovery() {
  sequence++;
  controller?.abort();
  discovering.value = false;
  choices.value = [];
  discoveryMessage.value = '';
  discoveryError.value = '';
}
watch(
  () => [
    props.value.id,
    props.value.kind,
    JSON.stringify(props.value.credentials),
    props.value.config.tenant_id,
    props.value.config.site_id,
  ],
  clearDiscovery,
);
onBeforeUnmount(clearDiscovery);
const selected = computed({
  get: () => (graph.value ? props.value.config.drive_id : props.value.config.bucket),
  set: (value: string) => {
    props.value.config[graph.value ? 'drive_id' : 'bucket'] = value;
  },
});
function choose(value: string) {
  const item = choices.value.find((item) => (graph.value ? item.id : item.name) === value);
  if (!item || graph.value) return;
  if (item.region) props.value.config.region = item.region;
  if (item.endpoint) props.value.config.endpoint = item.endpoint;
}
async function discover() {
  if (discovering.value) return;
  discoveryError.value = '';
  discoveryMessage.value = '';
  const ticket = ++sequence;
  controller?.abort();
  const current = new AbortController();
  controller = current;
  const timeout = setTimeout(() => current.abort(), 60000);
  discovering.value = true;
  try {
    const body = storageDiscoveryPayload(props.value);
    const result = await api<{ items: Choice[]; message?: string; truncated?: boolean }>(
      '/admin/storage/discover',
      { method: 'POST', body: JSON.stringify(body), signal: current.signal },
    );
    if (ticket !== sequence) return;
    choices.value = result.items || [];
    discoveryMessage.value =
      result.message ||
      (choices.value.length
        ? `找到 ${choices.value.length} 个${graph.value ? '云盘 / 文档库' : '存储桶'}${result.truncated ? '，可手动填写其他名称' : '，请选择保存位置'}`
        : '没有列出可用位置，可以手动填写名称。');
    if (choices.value.length === 1 && !selected.value) {
      selected.value = (graph.value ? choices.value[0].id : choices.value[0].name) || '';
      choose(selected.value);
    }
  } catch (e) {
    if (ticket === sequence) discoveryError.value = errorText(e);
  } finally {
    clearTimeout(timeout);
    if (ticket === sequence) discovering.value = false;
  }
}
const partMiB = computed({
  get: () =>
    props.value.config.part_size == null ? undefined : props.value.config.part_size / 1024 ** 2,
  set: (value: number | undefined) => {
    props.value.config.part_size = value == null ? undefined : value * 1024 ** 2;
  },
});
const endpointExamples: Partial<Record<StorageKind, string>> = {
  oss: 'https://oss-cn-hangzhou.aliyuncs.com',
  cos: 'https://cos.ap-guangzhou.myqcloud.com',
  obs: 'https://obs.cn-north-4.myhuaweicloud.com',
  minio: 'https://minio.example.com',
  rain: 'https://账户对应的 S3 服务地址',
  spaces: 'https://nyc3.digitaloceanspaces.com',
  r2: 'https://账户ID.r2.cloudflarestorage.com',
  oracle: 'https://命名空间.compat.objectstorage.区域.oraclecloud.com',
  b2: 'https://s3.us-west-004.backblazeb2.com',
  qiniu: 'https://s3.cn-east-1.qiniucs.com',
};
const endpointPlaceholder = computed(
  () => endpointExamples[props.value.kind] || 'Amazon S3 可留空，其他填写 S3 服务地址',
);
</script>
<template>
  <div class="form-two-columns">
    <el-form-item label="存储名称" required
      ><el-input v-model="value.name" maxlength="200" placeholder="例如：家庭 NAS、杭州 OSS"
    /></el-form-item>
    <el-form-item label="存储服务"
      ><el-select
        :model-value="value.kind"
        filterable
        @update:model-value="(kind) => changeStorageKind(value, kind)"
      >
        <el-option-group v-for="group in ['本地', '对象存储', '云盘']" :key="group" :label="group"
          ><el-option
            v-for="provider in storageProviders.filter((provider) => provider.group === group)"
            :key="provider.kind"
            :value="provider.kind"
            :label="provider.name"
        /></el-option-group> </el-select
    ></el-form-item>
  </div>
  <template v-if="value.kind === 'local'">
    <el-form-item label="媒体目录"
      ><el-input v-model="value.config.root" placeholder="留空使用部署的媒体根目录" />
      <p class="field-help">
        填写服务端挂载目录内的路径，例如 /data/media/archive。视频以稿件与 UP 主命名，方便在 NAS
        中浏览。
      </p></el-form-item
    >
  </template>
  <template v-else>
    <div v-if="!graph && value.kind !== 'upyun'" class="form-two-columns">
      <el-form-item label="API 服务地址 · Endpoint" :required="value.kind !== 's3'"
        ><el-input
          v-model="value.config.endpoint"
          :placeholder="endpointPlaceholder"
          @input="clearDiscovery"
      /></el-form-item>
      <el-form-item label="区域 · Region"
        ><el-input
          v-model="value.config.region"
          :placeholder="
            value.kind === 'oss'
              ? '可从 OSS 标准 API 地址识别，如 cn-hangzhou'
              : value.kind === 'r2'
                ? 'auto'
                : '选择存储桶时可自动填入'
          "
      /></el-form-item>
    </div>
    <template v-if="graph">
      <el-form-item label="租户"
        ><el-input
          v-model="value.config.tenant_id"
          :placeholder="
            value.kind.endsWith('_cn')
              ? 'organizations，或组织的 Tenant ID'
              : 'common，或组织的 Tenant ID'
          "
      /></el-form-item>
      <el-form-item v-if="value.kind.startsWith('sharepoint')" label="SharePoint 站点" required
        ><el-input
          v-model="value.config.site_id"
          placeholder="例如 example.sharepoint.com:/sites/archive，或 Site ID"
      /></el-form-item>
    </template>
    <fieldset class="storage-credentials">
      <legend>访问凭据</legend>
      <p class="field-help">
        {{
          value.editing && value.hasCredentials && !value.credentialsChangedKind
            ? '留空保留已有凭据；修改时填写完整一组。'
            : graph
              ? '填写自己的 Microsoft 应用凭据与授权后取得的刷新令牌。'
              : '填写具有此保存位置读写权限的凭据。'
        }}
      </p>
      <el-form-item v-for="field in fields" :key="field.key" :label="field.label"
        ><el-input
          v-model="value.credentials[field.key]"
          type="password"
          show-password
          autocomplete="new-password"
      /></el-form-item>
    </fieldset>
    <el-form-item
      :label="graph ? '云盘 / 文档库' : value.kind === 'upyun' ? '服务名称' : '存储桶 · Bucket'"
      required
    >
      <div class="storage-discovery">
        <el-select
          v-model="selected"
          filterable
          allow-create
          default-first-option
          :placeholder="
            graph ? '获取列表后选择，或直接填写 Drive ID' : '获取列表后选择，或直接填写名称'
          "
          @change="choose"
          ><el-option
            v-for="choice in choices"
            :key="choice.id || choice.name"
            :value="graph ? choice.id! : choice.name"
            :label="choice.name"
            ><span>{{ choice.name }}</span
            ><small v-if="choice.region">{{ choice.region }}</small></el-option
          ></el-select
        ><el-button v-if="value.kind !== 'upyun'" :loading="discovering" @click="discover">{{
          discovering ? '获取中' : '获取列表'
        }}</el-button>
      </div>
      <p v-if="discoveryMessage" class="field-help" role="status">{{ discoveryMessage }}</p>
      <p v-if="value.kind === 'upyun'" class="field-help">
        又拍云不提供服务枚举，请填写控制台中的服务名称。
      </p>
    </el-form-item>
    <el-alert
      v-if="discoveryError"
      :title="discoveryError"
      type="error"
      :closable="false"
      class="discovery-error"
    />
    <el-form-item label="保存子目录（可选）"
      ><el-input v-model="value.config.prefix" placeholder="例如 treasure-up；不填则保存到根目录"
    /></el-form-item>
    <section class="storage-delivery">
      <h3>视频播放</h3>
      <el-form-item label="传输方式"
        ><el-select
          v-model="value.config.delivery_mode"
          placeholder="自动：云端直连，本地由本站读取"
          clearable
          ><el-option label="自动选择" value="auto" /><el-option
            label="浏览器直接读取云端"
            value="direct" /><el-option label="经过本站地址跳转到云端" value="redirect"
        /></el-select>
        <p class="field-help">
          云端直连和地址跳转均由存储服务传输视频，本站只负责授权与播放清单。
        </p></el-form-item
      >
      <template v-if="!graph">
        <el-form-item label="访问权限"
          ><el-select
            :model-value="value.config.private_bucket ?? true"
            @update:model-value="value.config.private_bucket = $event"
            ><el-option :value="true" label="私有空间 · 使用限时签名" /><el-option
              :value="false"
              label="公开域名 / CDN · 不附带存储签名" /></el-select
        ></el-form-item>
        <el-form-item
          label="自定义播放域名 / CDN"
          :required="value.kind === 'upyun' || value.config.private_bucket === false"
          ><el-input
            v-model="value.config.public_base_url"
            placeholder="https://video.example.com（已绑定此存储桶）"
          />
          <p class="field-help">
            填写桶绑定域名，不再重复填写桶名。COS 开启 CDN 回源鉴权时，应选择公开域名 / CDN。
          </p></el-form-item
        >
        <p class="field-help">
          网页直连需要存储服务允许本站跨域读取。本站使用 HTTPS 时，播放域名也须支持 HTTPS。
        </p>
      </template>
    </section>
  </template>
  <details class="storage-advanced">
    <summary>高级设置</summary>
    <el-form-item
      v-if="!graph && !['local', 'upyun', 'qiniu'].includes(value.kind)"
      label="签名外网服务地址（可选）"
      ><el-input
        v-model="value.config.public_endpoint"
        placeholder="服务器使用内网 Endpoint 时，填写对应的外网 API 地址"
      />
      <p class="field-help">
        这是同一桶的签名 API 入口；自定义域名和 CDN 请填写上方的播放域名。
      </p></el-form-item
    >
    <el-form-item v-if="s3Storage(value.kind)" label="桶寻址方式"
      ><el-select
        v-model="value.config.addressing_style"
        clearable
        placeholder="由存储服务默认设置决定"
        ><el-option label="虚拟主机式（bucket.host）" value="virtual" /><el-option
          label="路径式（host/bucket）"
          value="path" /><el-option label="由 SDK 自动选择" value="auto" /></el-select
    ></el-form-item>
    <el-form-item label="读取优先级"
      ><el-input-number
        v-model="value.config.read_priority"
        :min="0"
        :max="10000"
        placeholder="默认 100"
      />
      <p class="field-help">数字越小越优先；自动选路还会参考可用性和实际连接情况。</p></el-form-item
    >
    <el-form-item
      v-if="value.kind !== 'local' && !graph && value.kind !== 'upyun'"
      label="分片上传大小（MiB）"
      ><el-input-number v-model="partMiB" :min="5" :max="512" placeholder="默认 16"
    /></el-form-item>
  </details>
  <p v-if="value.editing" class="field-help">
    已有资产的位置不能直接改目录、桶或前缀。需要搬移时请新增位置并同步或迁移。
  </p>
  <div class="settings-switches">
    <el-checkbox v-model="value.is_default">设为默认写入位置</el-checkbox
    ><el-checkbox v-model="value.enabled">启用</el-checkbox>
  </div>
</template>
<style scoped>
.storage-credentials {
  border: 1px solid var(--border, #ddd);
  border-radius: 4px;
  padding: 0 14px 2px;
  margin: 4px 0 18px;
}
.storage-credentials legend {
  padding: 0 6px;
  font-size: 13px;
  color: var(--text);
}
.storage-credentials > p {
  margin: 2px 0 14px;
}
.storage-discovery {
  display: flex;
  gap: 8px;
  width: 100%;
}
.storage-discovery .el-select {
  flex: 1;
  min-width: 0;
}
.storage-discovery small {
  margin-left: 10px;
  color: var(--muted);
}
.storage-delivery {
  padding-top: 4px;
  border-top: 1px solid var(--border, #ddd);
}
.storage-delivery h3 {
  margin: 12px 0 16px;
  font-size: 14px;
  font-weight: 600;
}
.storage-delivery > p {
  margin-top: -6px;
  margin-bottom: 18px;
}
.discovery-error {
  margin: -8px 0 16px;
}
.storage-advanced {
  margin: 18px 0;
}
.storage-advanced summary {
  cursor: pointer;
  margin-bottom: 14px;
  font-size: 13px;
  color: var(--muted);
}
</style>
