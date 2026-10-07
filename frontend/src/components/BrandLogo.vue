<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    variant?: 'mark' | 'lockup';
    size?: number | string;
    alt?: string;
    decorative?: boolean;
    surface?: 'transparent' | 'paper';
  }>(),
  { variant: 'mark', alt: 'Treasure Up', decorative: false, surface: 'transparent' },
);
const imageSize = computed(() => {
  const size = props.size;
  if (size === undefined) return undefined;
  return typeof size === 'number' ? `${size}px` : size;
});
const imageSizes = computed(() => {
  if (imageSize.value) return imageSize.value.includes('%') ? '100vw' : imageSize.value;
  return props.variant === 'lockup' ? '168px' : '(max-width: 700px) 36px, 44px';
});
const imageSources = computed(() =>
  [128, 256, 512, 1024]
    .map(
      (width) => `/brand/logo-${props.variant}${width === 1024 ? '' : `-${width}`}.png ${width}w`,
    )
    .join(', '),
);
</script>

<template>
  <img
    class="brand-logo"
    :class="[`brand-logo--${variant}`, { 'brand-logo--paper': surface === 'paper' }]"
    :src="`/brand/logo-${variant}-${variant === 'mark' ? 128 : 256}.png`"
    :srcset="imageSources"
    :sizes="imageSizes"
    :alt="decorative ? '' : alt"
    :aria-hidden="decorative || undefined"
    :style="{ '--brand-logo-size': imageSize }"
    width="512"
    height="512"
    decoding="async"
    draggable="false"
  />
</template>

<style scoped>
.brand-logo {
  display: inline-block;
  inline-size: var(--brand-logo-size, var(--brand-logo-default-size, 44px));
  block-size: auto;
  aspect-ratio: 1;
  max-inline-size: 100%;
  object-fit: contain;
  vertical-align: middle;
  flex-shrink: 0;
  user-select: none;
}
.brand-logo--lockup {
  --brand-logo-default-size: 168px;
}
.brand-logo--paper {
  padding: 8px;
  border-radius: 16px;
  background: #fff;
}
@media (prefers-color-scheme: dark) {
  .brand-logo--mark {
    filter: drop-shadow(0 0 1px #ffffff70);
  }
}
@media (forced-colors: active) {
  .brand-logo {
    forced-color-adjust: none;
  }
}
</style>
