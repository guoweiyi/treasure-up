<script setup lang="ts">
import { useId } from 'vue';
type Value = string | number;
const props = defineProps<{
  label: string;
  modelValue: Value;
  choices: { value: Value; label: string; disabled?: boolean }[];
  disabled?: boolean;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: Value] }>();
const id = useId();
function move(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key))
    return;
  event.preventDefault();
  const buttons = [
    ...(event.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>(
      'button:not(:disabled)',
    ),
  ];
  const current = buttons.indexOf(event.target as HTMLButtonElement);
  const index =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? buttons.length - 1
        : (current + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + buttons.length) %
          buttons.length;
  buttons[index]?.focus();
  buttons[index]?.click();
}
function tabIndex(index: number) {
  const selected = props.choices.findIndex(
    (choice) => choice.value === props.modelValue && !choice.disabled,
  );
  return index ===
    (selected >= 0 ? selected : props.choices.findIndex((choice) => !choice.disabled))
    ? 0
    : -1;
}
</script>
<template>
  <fieldset class="player-choice" :disabled="disabled">
    <legend :id="id">{{ label }}</legend>
    <div class="player-choice-buttons" role="radiogroup" :aria-labelledby="id" @keydown="move">
      <button
        v-for="(choice, index) in choices"
        :key="choice.value"
        type="button"
        role="radio"
        :aria-checked="modelValue === choice.value"
        :tabindex="tabIndex(index)"
        :disabled="disabled || choice.disabled"
        @click="emit('update:modelValue', choice.value)"
      >
        {{ choice.label }}
      </button>
    </div>
  </fieldset>
</template>
