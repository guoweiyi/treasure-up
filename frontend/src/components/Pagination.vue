<script setup lang="ts">
defineProps<{ page: number; total: number; pageSize?: number }>();
const emit = defineEmits<{ change: [page: number] }>();
</script>
<template>
  <nav v-if="total > (pageSize || 24)" class="pagination" aria-label="分页">
    <button :disabled="page <= 1" @click="emit('change', page - 1)">上一页</button
    ><span>第 {{ page }} / {{ Math.ceil(total / (pageSize || 24)) }} 页</span
    ><button :disabled="page * (pageSize || 24) >= total" @click="emit('change', page + 1)">
      下一页
    </button>
  </nav>
</template>
