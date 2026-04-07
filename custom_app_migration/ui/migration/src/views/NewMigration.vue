<template>
  <div class="max-w-4xl mx-auto px-6 py-8">
    <router-link to="/" class="inline-flex items-center gap-1 text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 mb-6">
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
      Back
    </router-link>

    <StepIndicator :steps="stepLabels" :currentStep="step" @go="goToStep" />

    <!-- Step 1: Source -->
    <div v-if="step === 0">
      <h2 class="text-xl font-bold mb-1">Where is your data?</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">Choose the system you're migrating from.</p>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
        <SourceCard title="Tally" subtitle="TallyPrime / ERP 9" :selected="source === 'tally'" @select="source = 'tally'" iconBg="bg-blue-50 dark:bg-blue-950">
          <template #icon><svg class="w-6 h-6 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="18" rx="2"/><path d="M8 7v10M12 7v10M16 7v10"/></svg></template>
        </SourceCard>
        <SourceCard title="SAP" subtitle="SAP B1 / S/4HANA" disabled iconBg="bg-orange-50 dark:bg-orange-950">
          <template #icon><svg class="w-6 h-6 text-orange-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg></template>
        </SourceCard>
        <SourceCard title="QuickBooks" subtitle="Online / Desktop" disabled iconBg="bg-green-50 dark:bg-green-950">
          <template #icon><svg class="w-6 h-6 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M9 12h6M12 9v6"/></svg></template>
        </SourceCard>
        <SourceCard title="CSV / Excel" subtitle="Import from files" disabled iconBg="bg-purple-50 dark:bg-purple-950">
          <template #icon><svg class="w-6 h-6 text-purple-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></template>
        </SourceCard>
      </div>

      <!-- Tally connection options -->
      <div v-if="source === 'tally'" class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-6">
        <h3 class="font-semibold text-sm mb-4">Tally Connection</h3>

        <div class="flex gap-3 mb-5">
          <button
            v-for="opt in ['live', 'upload']"
            :key="opt"
            @click="tallyMode = opt"
            :class="[
              'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
              tallyMode === opt
                ? 'bg-primary-500 text-white'
                : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700'
            ]"
          >
            {{ opt === 'live' ? 'Live Server (XML API)' : 'Upload XML Export' }}
          </button>
        </div>

        <!-- Live server -->
        <div v-if="tallyMode === 'live'" class="space-y-4">
          <div class="flex gap-3">
            <div class="flex-1">
              <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Host / IP Address</label>
              <input
                v-model="tallyHost"
                type="text"
                placeholder="10.211.55.3"
                class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
            </div>
            <div class="w-28">
              <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Port</label>
              <input
                v-model.number="tallyPort"
                type="number"
                placeholder="9000"
                class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
            </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              @click="testConnection"
              :disabled="testingConnection || !tallyHost"
              class="px-4 py-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 rounded-lg text-sm font-medium hover:bg-gray-700 dark:hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {{ testingConnection ? 'Testing...' : 'Test Connection' }}
            </button>
            <span v-if="connectionStatus === 'ok'" class="flex items-center gap-1.5 text-sm text-green-600 dark:text-green-400">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
              Connected &middot; {{ connectionCompany }}
            </span>
            <span v-else-if="connectionStatus === 'error'" class="flex items-center gap-1.5 text-sm text-red-500">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              {{ connectionError }}
            </span>
          </div>
        </div>

        <!-- Upload mode -->
        <div v-else class="space-y-3">
          <label class="flex flex-col items-center justify-center p-8 border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl cursor-pointer hover:border-primary-400 transition-colors">
            <svg class="w-8 h-8 text-gray-300 dark:text-gray-600 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            <span class="text-sm text-gray-500 dark:text-gray-400">Drop XML file here or click to browse</span>
            <input type="file" accept=".xml" class="hidden" @change="handleFileUpload" />
          </label>
          <p v-if="uploadedFile" class="text-sm text-green-600 dark:text-green-400">Loaded: {{ uploadedFile.name }}</p>
        </div>
      </div>

      <div class="flex justify-end mt-8">
        <button
          @click="step = 1"
          :disabled="!canProceedFromSource"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >Continue</button>
      </div>
    </div>

    <!-- Step 2: Target -->
    <div v-if="step === 1">
      <h2 class="text-xl font-bold mb-1">Where should the data go?</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">Choose the target agent-app.</p>

      <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-8">
        <SourceCard title="SenaERP" subtitle="Accounting, Inventory, CRM" :selected="target === 'erpnext'" @select="target = 'erpnext'" iconBg="bg-blue-50 dark:bg-blue-950">
          <template #icon><svg class="w-6 h-6 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></template>
        </SourceCard>
        <SourceCard title="Comms" subtitle="Messaging & contacts" disabled iconBg="bg-violet-50 dark:bg-violet-950">
          <template #icon><svg class="w-6 h-6 text-violet-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></template>
        </SourceCard>
        <SourceCard title="Dojo" subtitle="Personal life OS" disabled iconBg="bg-amber-50 dark:bg-amber-950">
          <template #icon><svg class="w-6 h-6 text-amber-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></template>
        </SourceCard>
      </div>

      <div v-if="target === 'erpnext'" class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-6 space-y-4">
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Company Name</label>
          <input
            v-model="companyName"
            type="text"
            placeholder="Avinash Industries"
            class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>
        <div>
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Company Abbreviation</label>
          <input
            v-model="companyAbbr"
            type="text"
            placeholder="AI"
            maxlength="5"
            class="w-32 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent uppercase"
          />
        </div>
      </div>

      <div class="flex justify-between mt-8">
        <button @click="step = 0" class="px-6 py-2.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">Back</button>
        <button
          @click="fetchPreview"
          :disabled="!canProceedFromTarget"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >{{ fetchingPreview ? 'Fetching Data...' : 'Continue' }}</button>
      </div>
    </div>

    <!-- Step 3: Data Preview -->
    <div v-if="step === 2">
      <h2 class="text-xl font-bold mb-1">Data Preview</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">Review what will be migrated. Uncheck items you want to skip.</p>

      <div class="space-y-3">
        <div
          v-for="entity in previewEntities"
          :key="entity.key"
          class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden"
        >
          <div class="flex items-center gap-3 px-5 py-3.5">
            <input
              type="checkbox"
              v-model="entity.enabled"
              class="w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-500"
            />
            <div class="flex-1">
              <span class="font-medium text-sm">{{ entity.label }}</span>
            </div>
            <span class="text-sm font-mono text-gray-400">{{ entity.count.toLocaleString() }}</span>
            <button
              @click="entity.expanded = !entity.expanded"
              class="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              <svg
                :class="['w-4 h-4 text-gray-400 transition-transform', entity.expanded ? 'rotate-180' : '']"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
              ><polyline points="6 9 12 15 18 9"/></svg>
            </button>
          </div>
          <div v-if="entity.expanded && entity.samples.length" class="border-t border-gray-100 dark:border-gray-800 px-5 py-3 bg-gray-50 dark:bg-gray-950">
            <table class="w-full text-xs">
              <tbody>
                <tr v-for="(sample, si) in entity.samples" :key="si" class="border-b border-gray-100 dark:border-gray-800 last:border-0">
                  <td class="py-1.5 pr-4 text-gray-400 font-mono">{{ si + 1 }}</td>
                  <td class="py-1.5 font-medium">{{ sample.name || sample }}</td>
                  <td v-if="sample.parent" class="py-1.5 text-gray-400">{{ sample.parent }}</td>
                  <td v-if="sample.balance != null" class="py-1.5 text-right font-mono">{{ formatNumber(sample.balance) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="flex justify-between mt-8">
        <button @click="step = 1" class="px-6 py-2.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">Back</button>
        <button
          @click="startMigration"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium transition-colors"
        >Start Migration</button>
      </div>
    </div>

    <!-- Step 4: Progress -->
    <div v-if="step === 3">
      <h2 class="text-xl font-bold mb-1">Migration in Progress</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">Please wait while your data is being imported.</p>

      <!-- Overall progress -->
      <div class="mb-8">
        <div class="flex justify-between text-sm mb-2">
          <span class="font-medium">Overall Progress</span>
          <span class="text-gray-400">{{ overallProgress }}%</span>
        </div>
        <div class="w-full h-2 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
          <div
            class="h-full bg-primary-500 rounded-full transition-all duration-500"
            :style="{ width: overallProgress + '%' }"
          />
        </div>
      </div>

      <!-- Step checklist -->
      <div class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 divide-y divide-gray-100 dark:divide-gray-800 mb-6">
        <div
          v-for="(task, i) in migrationTasks"
          :key="i"
          class="flex items-center gap-3 px-5 py-3"
        >
          <div class="flex-shrink-0">
            <svg v-if="task.status === 'done'" class="w-5 h-5 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            <svg v-else-if="task.status === 'error'" class="w-5 h-5 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            <svg v-else-if="task.status === 'running'" class="w-5 h-5 text-primary-500 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2a10 10 0 0 1 10 10" stroke-linecap="round"/></svg>
            <div v-else class="w-5 h-5 rounded-full border-2 border-gray-300 dark:border-gray-600" />
          </div>
          <span :class="['text-sm flex-1', task.status === 'pending' ? 'text-gray-400' : 'text-gray-900 dark:text-gray-100']">
            {{ task.label }}
          </span>
          <span v-if="task.detail" class="text-xs text-gray-400 font-mono">{{ task.detail }}</span>
        </div>
      </div>

      <!-- Log panel -->
      <div class="bg-gray-900 rounded-xl p-4 max-h-48 overflow-y-auto font-mono text-xs text-gray-400">
        <div v-for="(log, i) in migrationLogs" :key="i" class="py-0.5">
          <span class="text-gray-600">{{ log.time }}</span>
          <span :class="log.level === 'error' ? 'text-red-400' : log.level === 'success' ? 'text-green-400' : 'text-gray-400'">
            {{ ' ' + log.message }}
          </span>
        </div>
        <div v-if="!migrationLogs.length" class="text-gray-600">Waiting for migration to start...</div>
      </div>
    </div>

    <!-- Step 5: Validation -->
    <div v-if="step === 4">
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-50 dark:bg-green-950 mb-4">
          <svg class="w-8 h-8 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <h2 class="text-xl font-bold mb-1">Migration Complete</h2>
        <p class="text-gray-500 dark:text-gray-400 text-sm">Your data has been imported successfully.</p>
      </div>

      <!-- Summary cards -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
        <div v-for="stat in validationStats" :key="stat.label" class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 p-4 text-center">
          <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stat.value.toLocaleString() }}</p>
          <p class="text-xs text-gray-400 mt-1">{{ stat.label }}</p>
        </div>
      </div>

      <!-- Errors/Warnings -->
      <div v-if="validationErrors.length" class="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-xl p-5 mb-6">
        <h3 class="font-semibold text-sm text-red-700 dark:text-red-300 mb-2">{{ validationErrors.length }} errors during import</h3>
        <ul class="space-y-1">
          <li v-for="(err, i) in validationErrors.slice(0, 10)" :key="i" class="text-xs text-red-600 dark:text-red-400">{{ err }}</li>
          <li v-if="validationErrors.length > 10" class="text-xs text-red-500">... and {{ validationErrors.length - 10 }} more</li>
        </ul>
      </div>

      <div class="flex justify-center mt-8">
        <router-link
          to="/"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium transition-colors"
        >Done</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import StepIndicator from '@/components/StepIndicator.vue'
import SourceCard from '@/components/SourceCard.vue'

const stepLabels = ['Source', 'Target', 'Preview', 'Migrate', 'Validate']
const step = ref(0)

// Step 1: Source
const source = ref('')
const tallyMode = ref('live')
const tallyHost = ref('')
const tallyPort = ref(9000)
const testingConnection = ref(false)
const connectionStatus = ref('')
const connectionCompany = ref('')
const connectionError = ref('')
const uploadedFile = ref(null)

const canProceedFromSource = computed(() => {
  if (source.value !== 'tally') return false
  if (tallyMode.value === 'live') return connectionStatus.value === 'ok'
  return !!uploadedFile.value
})

async function testConnection() {
  testingConnection.value = true
  connectionStatus.value = ''
  connectionError.value = ''
  try {
    // Simulate connection test (will call real API when backend is ready)
    await new Promise(r => setTimeout(r, 1500))
    // Mock success for now
    connectionStatus.value = 'ok'
    connectionCompany.value = 'Avinash Industries - Chennai Unit - 2025-26'
  } catch (e) {
    connectionStatus.value = 'error'
    connectionError.value = e.message || 'Connection failed'
  } finally {
    testingConnection.value = false
  }
}

function handleFileUpload(event) {
  const file = event.target.files?.[0]
  if (file) uploadedFile.value = file
}

// Step 2: Target
const target = ref('')
const companyName = ref('')
const companyAbbr = ref('')
const fetchingPreview = ref(false)

const canProceedFromTarget = computed(() => {
  return target.value === 'erpnext' && companyName.value.trim() && companyAbbr.value.trim()
})

async function fetchPreview() {
  fetchingPreview.value = true
  try {
    // Simulate fetching data preview (will call real API)
    await new Promise(r => setTimeout(r, 2000))
    // Mock preview data
    previewEntities.value = [
      { key: 'groups', label: 'Account Groups', count: 282, enabled: true, expanded: false, samples: [
        { name: 'Capital Account' }, { name: 'Current Assets' }, { name: 'Current Liabilities' }, { name: 'Direct Expenses' }, { name: 'Fixed Assets' }
      ]},
      { key: 'ledgers', label: 'Ledger Accounts', count: 2943, enabled: true, expanded: false, samples: [
        { name: 'Cash', parent: 'Cash-in-Hand', balance: 125430 },
        { name: 'CUB 69472-CA', parent: 'Bank Accounts', balance: -5513.63 },
        { name: 'HDFC Bank', parent: 'Bank Accounts', balance: 892341 },
      ]},
      { key: 'customers', label: 'Customers', count: 153, enabled: true, expanded: false, samples: [
        { name: 'Bajaj Electricals Ltd' }, { name: 'Havells India Ltd' }, { name: 'Crompton Greaves' },
      ]},
      { key: 'suppliers', label: 'Suppliers', count: 1714, enabled: true, expanded: false, samples: [
        { name: 'Tata Steel Ltd' }, { name: 'JSW Steel' }, { name: 'Hindustan Zinc' },
      ]},
      { key: 'items', label: 'Stock Items', count: 168, enabled: true, expanded: false, samples: [
        { name: 'A300 01.Finished Goods', parent: 'Finished Goods' },
        { name: 'A300 02.Glass', parent: 'Raw Materials' },
        { name: 'A300 03.Brass Items', parent: 'Raw Materials' },
      ]},
      { key: 'units', label: 'Units of Measure', count: 12, enabled: true, expanded: false, samples: [
        { name: 'Nos' }, { name: 'Kgs' }, { name: 'Ltrs' },
      ]},
      { key: 'godowns', label: 'Warehouses', count: 8, enabled: true, expanded: false, samples: [
        { name: 'Main Location' }, { name: 'Chennai Warehouse' },
      ]},
    ]
    step.value = 2
  } catch (e) {
    alert('Failed to fetch data: ' + e.message)
  } finally {
    fetchingPreview.value = false
  }
}

// Step 3: Preview
const previewEntities = ref([])

function formatNumber(n) {
  if (n == null) return ''
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(n)
}

// Step 4: Progress
const migrationTasks = ref([])
const migrationLogs = ref([])

const overallProgress = computed(() => {
  if (!migrationTasks.value.length) return 0
  const done = migrationTasks.value.filter(t => t.status === 'done').length
  return Math.round((done / migrationTasks.value.length) * 100)
})

async function startMigration() {
  step.value = 3
  const enabledEntities = previewEntities.value.filter(e => e.enabled)

  migrationTasks.value = [
    { label: 'Creating Company', status: 'pending', detail: '' },
    ...enabledEntities.map(e => ({
      label: `Importing ${e.label}`,
      status: 'pending',
      detail: `${e.count.toLocaleString()} records`,
    })),
    { label: 'Setting Opening Balances', status: 'pending', detail: '' },
  ]

  // Simulate migration progress
  for (let i = 0; i < migrationTasks.value.length; i++) {
    migrationTasks.value[i].status = 'running'
    const label = migrationTasks.value[i].label
    migrationLogs.value.push({ time: new Date().toLocaleTimeString(), message: `Starting: ${label}`, level: 'info' })
    await new Promise(r => setTimeout(r, 800 + Math.random() * 1200))
    migrationTasks.value[i].status = 'done'
    migrationLogs.value.push({ time: new Date().toLocaleTimeString(), message: `Completed: ${label}`, level: 'success' })
  }

  // Move to validation
  await new Promise(r => setTimeout(r, 500))
  validationStats.value = enabledEntities.map(e => ({ label: e.label, value: e.count }))
  step.value = 4
}

// Step 5: Validation
const validationStats = ref([])
const validationErrors = ref([])

function goToStep(i) {
  if (i <= step.value) step.value = i
}
</script>
