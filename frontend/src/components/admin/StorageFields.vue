<script setup lang="ts">
import { computed } from 'vue';
import { ElCheckbox, ElFormItem, ElInput, ElInputNumber, ElOption, ElSelect } from 'element-plus';
import { changeStorageKind, type StorageDraft } from '../../utils/storageForm';
const props = defineProps<{ value: StorageDraft }>();
const partMiB = computed({
  get: () =>
    props.value.config.part_size == null ? undefined : props.value.config.part_size / 1024 ** 2,
  set: (value: number | undefined) => {
    props.value.config.part_size = value == null ? undefined : value * 1024 ** 2;
  },
});
const secretKey = computed(() =>
  props.value.kind === 's3' ? 'secret_access_key' : 'access_key_secret',
);
const tokenKey = computed(() => (props.value.kind === 's3' ? 'session_token' : 'security_token'));
const endpointPlaceholder = computed(() =>
  props.value.kind === 's3'
    ? 'https://s3.example.com（AWS 可留空）'
    : 'https://oss-cn-hangzhou.aliyuncs.com',
);
const regionPlaceholder = computed(() =>
  props.value.kind === 's3' ? '留空使用 us-east-1' : 'cn-hangzhou',
);
</script>
<template>
  <el-form-item label="存储名称" required
    ><el-input v-model="value.name" maxlength="200"
  /></el-form-item>
  <el-form-item label="类型"
    ><el-select
      :model-value="value.kind"
      @update:model-value="(kind) => changeStorageKind(value, kind)"
    >
      <el-option value="local" label="本地目录" /><el-option
        value="s3"
        label="S3 兼容存储"
      /><el-option value="oss" label="阿里云原生 OSS" /> </el-select
  ></el-form-item>
  <template v-if="value.kind === 'local'">
    <el-form-item label="媒体目录"
      ><el-input v-model="value.config.root" placeholder="留空使用部署的媒体根目录" />
      <p class="field-help">
        填写服务端挂载目录内的路径，例如 /data/media/archive；这里不是浏览器所在电脑的目录。
      </p>
    </el-form-item>
  </template>
  <template v-else>
    <el-form-item label="Bucket · 存储桶" required
      ><el-input v-model="value.config.bucket" placeholder="存储桶名称，不含协议或路径"
    /></el-form-item>
    <div class="form-two-columns">
      <el-form-item label="Endpoint · 服务端访问地址" :required="value.kind === 'oss'"
        ><el-input v-model="value.config.endpoint" :placeholder="endpointPlaceholder"
      /></el-form-item>
      <el-form-item label="Region · 区域" :required="value.kind === 'oss'"
        ><el-input v-model="value.config.region" :placeholder="regionPlaceholder"
      /></el-form-item>
    </div>
    <el-form-item label="播放 Endpoint（可选）"
      ><el-input
        v-model="value.config.public_endpoint"
        placeholder="浏览器可访问的 HTTPS 地址；留空复用服务端地址"
      />
      <p class="field-help">
        用于生成私有签名地址，应指向同一个桶。不能直接替换为不支持相同签名的普通 CDN 域名。
      </p></el-form-item
    >
    <el-form-item label="对象前缀（可选）"
      ><el-input v-model="value.config.prefix" placeholder="例如 treasure-up；不填则写入桶根目录"
    /></el-form-item>
    <el-form-item v-if="value.kind === 's3'" label="桶寻址方式"
      ><el-select v-model="value.config.addressing_style" clearable placeholder="默认：虚拟主机式">
        <el-option label="虚拟主机式（bucket.host）" value="virtual" /><el-option
          label="路径式（host/bucket）"
          value="path"
        /><el-option label="由 SDK 自动选择" value="auto" /> </el-select
    ></el-form-item>
    <fieldset class="storage-credentials">
      <legend>访问凭据</legend>
      <p class="field-help">
        {{
          value.editing && value.hasCredentials && !value.credentialsChangedKind
            ? '全部留空保留已有凭据。修改时请填写完整一组；已有秘密不会返回浏览器。'
            : '请填写此存储类型的完整凭据。切换类型后不会沿用旧凭据。'
        }}
      </p>
      <el-form-item label="Access Key ID"
        ><el-input
          v-model="value.credentials.access_key_id"
          type="password"
          show-password
          autocomplete="new-password"
      /></el-form-item>
      <el-form-item :label="value.kind === 's3' ? 'Secret Access Key' : 'Access Key Secret'"
        ><el-input
          v-model="value.credentials[secretKey]"
          type="password"
          show-password
          autocomplete="new-password"
      /></el-form-item>
      <el-form-item
        :label="
          value.kind === 's3' ? 'Session Token（临时凭据可选）' : 'Security Token（临时凭据可选）'
        "
        ><el-input
          v-model="value.credentials[tokenKey]"
          type="password"
          show-password
          autocomplete="new-password"
      /></el-form-item>
    </fieldset>
  </template>
  <details class="storage-advanced">
    <summary>高级调优</summary>
    <el-form-item label="读取优先级"
      ><el-input-number
        v-model="value.config.read_priority"
        :min="0"
        :max="10000"
        placeholder="默认 100"
      />
      <p class="field-help">
        数字越小，优先级越高；自动选路还会参考真实可用性和测量结果。
      </p></el-form-item
    >
    <el-form-item v-if="value.kind !== 'local'" label="分片上传大小（MiB）"
      ><el-input-number v-model="partMiB" :min="5" :max="512" placeholder="默认 16"
    /></el-form-item>
  </details>
  <p v-if="value.editing" class="field-help">
    已有资产的位置不能改目录、桶或前缀。需要搬移时请新增位置并执行同步或迁移。
  </p>
  <div class="settings-switches">
    <el-checkbox v-model="value.is_default">设为默认写入位置</el-checkbox
    ><el-checkbox v-model="value.enabled">启用</el-checkbox>
  </div>
</template>
<style scoped>
.storage-credentials {
  border: 1px solid var(--line, #ddd);
  border-radius: 9px;
  padding: 0 14px;
  margin: 8px 0 18px;
}
.storage-advanced {
  margin-bottom: 18px;
}
.storage-advanced summary {
  cursor: pointer;
  margin-bottom: 12px;
}
</style>
