<template>
  <div class="max-w-3xl mx-auto px-8 py-12">
    <!-- Hero -->
    <div class="text-center mb-12">
      <div class="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary-50 dark:bg-primary-950 mb-5">
        <svg class="w-8 h-8 text-primary-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2L2 7l10 5 10-5-10-5z"/>
          <path d="M2 17l10 5 10-5"/>
          <path d="M2 12l10 5 10-5"/>
        </svg>
      </div>
      <h2 class="text-2xl font-bold mb-2">Talk to Your Data</h2>
      <p class="text-gray-500 dark:text-gray-400 max-w-md mx-auto">
        Connect business systems and ask questions over fresh, synced data.
      </p>
    </div>

    <!-- New Migration CTA -->
    <div class="flex justify-center mb-12">
      <router-link
        to="/new"
        class="inline-flex items-center gap-2 px-6 py-3 bg-primary-500 hover:bg-primary-600 text-white rounded-lg font-medium transition-colors shadow-sm"
      >
        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="12" y1="5" x2="12" y2="19"/>
          <line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        Connect Data
      </router-link>
    </div>

    <!-- Connections -->
    <div>
      <h3 class="text-sm font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-4">Connected Sources</h3>

      <div v-if="loading" class="text-center py-8 text-gray-400">Loading...</div>

      <div v-else-if="!history.length" class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-8 text-center">
        <p class="text-gray-400 dark:text-gray-500">No connected sources yet. Connect Tally to start building your analytics replica.</p>
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="item in history"
          :key="item.name"
          class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 px-5 py-4 flex items-center gap-4"
        >
          <div class="flex-shrink-0">
            <span
              :class="[
                'inline-flex items-center justify-center w-9 h-9 rounded-lg text-sm font-medium',
                isHealthy(item.status) ? 'bg-green-50 text-green-600 dark:bg-green-950 dark:text-green-400' :
                isBroken(item.status) ? 'bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-400' :
                'bg-yellow-50 text-yellow-600 dark:bg-yellow-950 dark:text-yellow-400'
              ]"
            >
              <svg v-if="isHealthy(item.status)" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
              <svg v-else-if="isBroken(item.status)" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            </span>
          </div>
          <div class="flex-1 min-w-0">
            <p class="font-medium truncate">{{ item.connection_label || item.source_type }}</p>
            <p class="text-sm text-gray-400">{{ item.source_type }} &middot; {{ item.status }}<span v-if="item.tally_company_name"> &middot; {{ item.tally_company_name }}</span></p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listTydConnections } from '@/services/api.js'

const loading = ref(false)
const history = ref([])

function isHealthy(status) {
  return ['Active', 'Syncing', 'completed'].includes(status)
}

function isBroken(status) {
  return ['Error', 'Disabled', 'failed'].includes(status)
}

onMounted(async () => {
  loading.value = true
  try {
    history.value = await listTydConnections()
  } catch (e) {
    history.value = []
  } finally {
    loading.value = false
  }
})
</script>
