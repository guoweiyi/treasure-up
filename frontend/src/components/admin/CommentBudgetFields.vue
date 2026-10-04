<script setup lang="ts">
import type { Row } from '../../types';
import { commentBudgetFields, commentBudgetValue } from '../../utils/commentBudget';
defineProps<{ value: Row; inherit?: boolean }>();
const factor = (field: (typeof commentBudgetFields)[number]) =>
  'factor' in field ? field.factor : 1;
</script>
<template>
  <fieldset class="comment-budget" :disabled="value.fetch_comments === false">
    <legend>评论保存范围</legend>
    <p class="field-help">
      按有限扫描样本的点赞数优先保存。已有归档保留并占用额度，调整上限不会删除历史评论或文件。
    </p>
    <div class="comment-budget-grid">
      <label v-for="field in commentBudgetFields.slice(0, 2)" :key="field.key">
        {{ field.label }}
        <input
          type="number"
          :value="value[field.key] ?? ''"
          :min="field.min"
          :max="field.max"
          :step="field.step"
          :required="!inherit"
          :placeholder="inherit ? '使用系统设置' : ''"
          @input="value[field.key] = commentBudgetValue(($event.target as HTMLInputElement).value)"
        />
      </label>
    </div>
    <details class="comment-budget-more">
      <summary>回复与图片素材</summary>
      <div class="comment-budget-grid">
        <label v-for="field in commentBudgetFields.slice(2)" :key="field.key">
          {{ field.label }}
          <input
            type="number"
            :value="value[field.key] == null ? '' : value[field.key] / factor(field)"
            :min="field.min"
            :max="field.max"
            :step="field.step"
            :required="!inherit"
            :placeholder="inherit ? '使用系统设置' : ''"
            @input="
              value[field.key] = commentBudgetValue(
                ($event.target as HTMLInputElement).value,
                factor(field),
              )
            "
          />
        </label>
      </div>
      <p class="field-help">
        素材包含评论头像、表情和附图，同一地址只计一次。素材额度按视频累计，已有库存计入，任务续跑或重新采集均不重置；设为
        0 可不再新增对应内容。
      </p>
    </details>
    <p v-if="inherit" class="field-help">留空沿用系统设置。</p>
  </fieldset>
</template>
<style scoped>
.comment-budget {
  min-width: 0;
  border: 1px solid var(--line, #e3e5e7);
  border-radius: 8px;
  padding: 16px;
  margin: 18px 0;
}
.comment-budget legend {
  font-weight: 600;
  padding: 0 6px;
}
.comment-budget:disabled {
  opacity: 0.6;
}
.comment-budget-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 18px;
}
.comment-budget-grid label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-size: 13px;
}
.comment-budget-grid input {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 9px 11px;
  border: 1px solid var(--line, #dcdfe6);
  border-radius: 6px;
  background: var(--panel, #fff);
  color: inherit;
}
.comment-budget-more {
  margin-top: 16px;
}
.comment-budget-more summary {
  cursor: pointer;
  margin-bottom: 12px;
}
@media (max-width: 560px) {
  .comment-budget-grid {
    grid-template-columns: 1fr;
  }
}
</style>
