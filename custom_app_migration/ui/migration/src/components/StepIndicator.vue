<template>
  <nav class="flex items-center justify-center gap-1 mb-10">
    <template v-for="(step, i) in steps" :key="i">
      <div class="flex items-center gap-2">
        <button
          @click="$emit('go', i)"
          :disabled="i > currentStep"
          :class="[
            'flex items-center justify-center w-8 h-8 rounded-full text-sm font-medium transition-colors',
            i < currentStep ? 'bg-primary-500 text-white' :
            i === currentStep ? 'bg-primary-500 text-white ring-4 ring-primary-100 dark:ring-primary-900' :
            'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
          ]"
        >
          <svg v-if="i < currentStep" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
          <span v-else>{{ i + 1 }}</span>
        </button>
        <span
          :class="[
            'text-sm font-medium hidden sm:inline',
            i <= currentStep ? 'text-gray-900 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'
          ]"
        >{{ step }}</span>
      </div>
      <div
        v-if="i < steps.length - 1"
        :class="[
          'w-8 h-px mx-1',
          i < currentStep ? 'bg-primary-500' : 'bg-gray-200 dark:bg-gray-700'
        ]"
      />
    </template>
  </nav>
</template>

<script setup>
defineProps({
  steps: { type: Array, required: true },
  currentStep: { type: Number, required: true },
})
defineEmits(['go'])
</script>
