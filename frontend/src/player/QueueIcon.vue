<script setup lang="ts">
defineProps<{
  name:
    | 'previous'
    | 'next'
    | 'list'
    | 'pause'
    | 'repeat'
    | 'continuous'
    | 'chevron'
    | 'check'
    | 'playing';
}>();
const paths = {
  previous: 'M6 5v14M18 5l-9 7 9 7V5Z',
  next: 'M18 5v14M6 5l9 7-9 7V5Z',
  list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  pause: 'M8 5v14M16 5v14',
  repeat: 'm17 3 4 4-4 4M3 11V9a2 2 0 0 1 2-2h16M7 21l-4-4 4-4m14 0v2a2 2 0 0 1-2 2H3',
  continuous: 'M3 5h12M3 11h12M3 17h6m9-13v16m-4-4 4 4 4-4',
  chevron: 'm6 9 6 6 6-6',
  check: 'm5 12 4 4L19 6',
  playing: 'M6 9v6M12 5v14M18 8v8',
};
</script>
<template>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name]" />
  </svg>
</template>
