<template>
  <label
    :class="['flex items-center gap-3 px-4 py-3 border rounded-lg cursor-pointer transition-colors', file ? 'border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-950/30' : 'border-gray-200 dark:border-gray-700 hover:border-primary-300']"
  >
    <div class="flex-shrink-0">
      <svg v-if="file" class="w-4 h-4 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
      <svg v-else class="w-4 h-4 text-gray-300 dark:text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
    </div>
    <div class="flex-1 min-w-0">
      <span v-if="file" class="text-sm font-medium text-green-700 dark:text-green-300 truncate block">{{ file.name }}</span>
      <span v-else class="text-sm text-gray-500 dark:text-gray-400">{{ label }} <span class="text-gray-300 dark:text-gray-600">(.xlsx)</span></span>
    </div>
    <span v-if="file" class="text-xs text-green-600 dark:text-green-400 flex-shrink-0">{{ formatSize(file.size) }}</span>
    <span v-else-if="required" class="text-[10px] text-red-400 flex-shrink-0">Required</span>
    <span v-else class="text-[10px] text-gray-300 dark:text-gray-600 flex-shrink-0">Optional</span>
    <input type="file" :accept="accept" class="hidden" @change="onFile" />
  </label>
</template>

<script setup>
const props = defineProps({
  label: String,
  accept: { type: String, default: '.xlsx,.xls' },
  file: { type: [File, null], default: null },
  required: Boolean,
})
const emit = defineEmits(['change'])

function onFile(e) {
  emit('change', e.target.files?.[0] || null)
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}
</script>
