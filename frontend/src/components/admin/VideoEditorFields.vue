<script setup lang="ts">
import { ref } from 'vue';
import { ElButton, ElFormItem, ElInput } from 'element-plus';
import type { Row } from '../../types';
import { duration } from '../../api';
import { videoTags } from '../../utils/videoEditor';
const props = defineProps<{ value: Row }>();
const tagInput = ref(''),
  tagError = ref('');
function addTag() {
  tagError.value = '';
  try {
    props.value.tags = videoTags(props.value.tags || [], tagInput.value);
    tagInput.value = '';
  } catch (error) {
    tagError.value = error instanceof Error ? error.message : '标签无法添加';
  }
}
function enterTag(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  event.stopPropagation();
  addTag();
}
function removeTag(tag: string) {
  props.value.tags = props.value.tags.filter((item: string) => item !== tag);
  tagError.value = '';
}
</script>
<template>
  <div class="video-editor">
    <div class="video-editor-preview">
      <img
        v-if="value.cover_url"
        :src="value.cover_url"
        alt="视频归档封面"
        loading="lazy"
        width="192"
        height="108"
      />
      <div v-else class="video-editor-no-cover">暂无封面</div>
      <div>
        <span class="video-editor-kicker">视频资料</span>
        <h3>{{ value.title || value.source_title }}</h3>
        <p class="muted small">
          {{ value.bvid }}<span v-if="value.duration"> · {{ duration(value.duration) }}</span>
        </p>
      </div>
    </div>
    <p class="field-help">整理后的标题、简介和标签会显示在视频页，后续同步会保留你的修改。</p>
    <el-form-item label="标题" required>
      <el-input v-model="value.title" maxlength="2000" placeholder="为视频起一个容易查找的标题" />
      <div class="video-editor-source">
        <span>来源：{{ value.source_title }}</span>
        <el-button
          v-if="value.title !== value.source_title"
          type="primary"
          link
          @click="value.title = value.source_title"
          >恢复来源标题</el-button
        >
      </div>
    </el-form-item>
    <el-form-item label="简介">
      <el-input
        v-model="value.description"
        type="textarea"
        :autosize="{ minRows: 4, maxRows: 9 }"
        maxlength="50000"
        placeholder="补充视频内容、看点或相关说明"
      />
      <el-button
        v-if="value.description !== value.source_description"
        type="primary"
        link
        class="video-editor-restore"
        @click="value.description = value.source_description"
        >恢复来源简介</el-button
      >
    </el-form-item>
    <el-form-item label="B 站标签">
      <div v-if="value.source_tags?.length" class="source-tags">
        <span v-for="tag in value.source_tags" :key="tag">{{ tag }}</span>
      </div>
      <p v-else class="field-help">尚未获取来源标签，下次采集时自动同步。</p>
    </el-form-item>
    <el-form-item label="自定义标签">
      <div class="video-tags" aria-label="视频标签">
        <span v-for="tag in value.tags" :key="tag" class="video-tag-chip"
          >{{ tag }}
          <button
            type="button"
            class="video-tag-remove"
            :aria-label="`移除标签 ${tag}`"
            @click="removeTag(tag)"
          >
            ×
          </button>
        </span>
        <input
          v-model="tagInput"
          type="text"
          aria-label="添加视频标签"
          placeholder="输入标签后按回车"
          maxlength="5000"
          @keydown.enter="enterTag"
        />
        <button type="button" class="video-tag-add" :disabled="!tagInput.trim()" @click="addTag">
          添加
        </button>
      </div>
      <p v-if="tagError" class="form-error" role="alert">{{ tagError }}</p>
      <p class="field-help">
        与 B 站标签一起展示。回车添加，也可用逗号分隔；自动同步会保留这些标签。
      </p>
    </el-form-item>
    <details class="video-editor-notes" :open="!!value.notes">
      <summary>整理笔记 <span class="muted small">仅管理员和内容编辑可见</span></summary>
      <el-input
        v-model="value.notes"
        type="textarea"
        :rows="3"
        maxlength="50000"
        placeholder="记录整理备注或后续处理事项"
        aria-label="整理笔记"
      />
    </details>
  </div>
</template>
<style scoped>
.video-editor-preview {
  display: grid;
  grid-template-columns: 176px minmax(0, 1fr);
  gap: 18px;
  align-items: center;
  margin-bottom: 14px;
}
.video-editor-preview img,
.video-editor-no-cover {
  width: 100%;
  height: auto;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: 8px;
  background: #f1f2f3;
}
.video-editor-no-cover {
  display: grid;
  place-items: center;
  color: #9499a0;
}
.video-editor-preview h3 {
  margin: 7px 0;
  font-size: 17px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.video-editor-preview p {
  margin: 0;
}
.video-editor-kicker {
  font-size: 12px;
  color: var(--accent, #00aeec);
}
.video-editor-source {
  display: flex;
  width: 100%;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: #9499a0;
  font-size: 12px;
  margin-top: 6px;
  overflow-wrap: anywhere;
}
.video-editor-source > span {
  flex: 1 1 220px;
}
.video-editor-restore {
  margin-top: 7px;
}
.video-tags {
  display: flex;
  width: 100%;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 8px;
}
.source-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.source-tags span {
  background: #f1f3f5;
  color: #61666d;
  padding: 2px 9px;
  border-radius: 4px;
  font-size: 12px;
}
.video-tags:focus-within {
  border-color: var(--accent, #00aeec);
}
.video-tags input {
  flex: 1 1 150px;
  min-width: 100px;
  width: 100px;
  border: 0;
  outline: none;
  padding: 3px 5px;
  color: inherit;
  background: transparent;
}
.video-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  overflow-wrap: anywhere;
  border-radius: 4px;
  background: #ecf5ff;
  color: #409eff;
  padding: 2px 7px;
  font-size: 13px;
  line-height: 24px;
}
.video-tag-remove {
  flex-shrink: 0;
  border: 0;
  color: inherit;
  background: none;
  padding: 0 2px;
  min-height: 0;
  line-height: 20px;
}
.video-tag-add {
  padding: 3px 10px;
  font-size: 12px;
}
.video-editor-notes {
  padding-top: 4px;
}
.video-editor-notes summary {
  cursor: pointer;
  margin-bottom: 12px;
}
.video-editor-notes summary span {
  margin-left: 8px;
}
@media (max-width: 540px) {
  .video-editor-preview {
    grid-template-columns: 112px minmax(0, 1fr);
    gap: 12px;
  }
  .video-editor-preview h3 {
    font-size: 14px;
  }
}
</style>
