<template>
  <button
    @click="!disabled && $emit('select')"
    :class="[
      'relative flex flex-col items-center gap-3 p-6 rounded-xl border-2 transition-all text-center',
      selected ? 'border-primary-500 bg-primary-50 dark:bg-primary-950 shadow-sm' :
      disabled ? 'border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 opacity-60 cursor-not-allowed' :
      'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 hover:border-primary-300 dark:hover:border-primary-700 hover:shadow-sm cursor-pointer'
    ]"
  >
    <div :class="['w-12 h-12 rounded-xl flex items-center justify-center', iconBg]">
      <slot name="icon" />
    </div>
    <div>
      <p class="font-semibold text-sm">{{ title }}</p>
      <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ subtitle }}</p>
    </div>
    <span
      v-if="disabled"
      class="absolute top-2 right-2 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
    >{{ disabledLabel }}</span>
    <span
      v-if="selected"
      class="absolute top-2 right-2 w-5 h-5 rounded-full bg-primary-500 flex items-center justify-center"
    >
      <svg class="w-3 h-3 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
    </span>
  </button>
</template>

<script setup>
defineProps({
  title: String,
  subtitle: String,
  selected: Boolean,
  disabled: Boolean,
  disabledLabel: { type: String, default: 'Coming Soon' },
  iconBg: { type: String, default: 'bg-gray-100 dark:bg-gray-800' },
})
defineEmits(['select'])
</script>
