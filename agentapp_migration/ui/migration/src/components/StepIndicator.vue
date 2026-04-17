<template>
  <nav class="flex items-center gap-0.5">
    <template v-for="(step, i) in steps" :key="i">
      <button
        @click="$emit('go', i)"
        :disabled="i > currentStep"
        :class="[
          'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors',
          i < currentStep ? 'text-primary-600 hover:bg-primary-50' :
          i === currentStep ? 'text-primary-600 font-semibold bg-primary-50' :
          'text-ink-faint cursor-default'
        ]"
      >
        <span
          :class="[
            'flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold',
            i < currentStep ? 'bg-primary-500 text-white' :
            i === currentStep ? 'bg-primary-500 text-white' :
            'bg-black/[0.06] text-ink-faint'
          ]"
        >
          <svg v-if="i < currentStep" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
          <span v-else>{{ i + 1 }}</span>
        </span>
        <span class="hidden sm:inline">{{ step }}</span>
      </button>
      <div
        v-if="i < steps.length - 1"
        :class="['w-4 h-px mx-0.5', i < currentStep ? 'bg-primary-300' : 'bg-black/[0.08]']"
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
