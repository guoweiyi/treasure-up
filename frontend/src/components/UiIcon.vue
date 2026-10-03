<script setup lang="ts">
defineProps<{
  name:
    | 'search'
    | 'play'
    | 'danmaku'
    | 'star'
    | 'folder'
    | 'settings'
    | 'arrow'
    | 'user'
    | 'like'
    | 'coin'
    | 'share';
}>();
const paths = {
  search: 'm20 20-4.5-4.5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
  play: 'M9 7v10l8-5-8-5ZM5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
  danmaku:
    'M5 3h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-5l-4 3v-3H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 5h3m4 0h3M7 13h10',
  star: 'm12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z',
  folder: 'M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z',
  settings: 'M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-7 0v6',
  arrow: 'm9 5 7 7-7 7',
  user: 'M8 7a4 4 0 1 0 8 0 4 4 0 0 0-8 0ZM4 21v-2a8 8 0 0 1 16 0v2',
  like: 'M7 10v11H3V10h4Zm0 1 5-8c2 0 3 1 2 4l-1 3h6c2 0 2 1 2 3l-2 6c0 1-1 2-2 2H7',
  coin: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM9 8h6v8H9V8Z',
  share: 'M4 19v-5a4 4 0 0 1 4-4h11m-5-5 5 5-5 5',
};
</script>
<template>
  <svg
    class="ui-icon"
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
