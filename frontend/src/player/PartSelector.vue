<script setup lang="ts">
import type { Part } from '../types';
import { duration } from '../api';
defineProps<{ parts: Part[]; selected: string }>();
defineEmits<{ select: [id: string] }>();
</script>
<template>
  <section class="sidebar-section parts-section" aria-label="视频选集">
    <h2>
      视频选集
      <span
        >{{ parts.find((item) => item.id === selected)?.position || 1 }} / {{ parts.length }}</span
      >
    </h2>
    <div class="part-list">
      <button
        v-for="item in parts"
        :key="item.id"
        :class="{ selected: item.id === selected }"
        :aria-current="item.id === selected ? 'true' : undefined"
        @click="$emit('select', item.id)"
      >
        <span class="part-number">P{{ item.position }}</span
        ><span class="part-name"
          >{{ item.title || 'P' + item.position
          }}<small v-if="!item.variants?.length">待归档</small></span
        ><span class="part-duration">{{ duration(item.duration) }}</span>
      </button>
    </div>
  </section>
</template>
