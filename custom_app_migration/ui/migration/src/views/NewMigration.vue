<template>
  <div>
    <!-- Header stepper -->
    <Teleport to="header .flex-1" v-if="headerMounted">
      <StepIndicator :steps="stepLabels" :currentStep="step" @go="goToStep" />
    </Teleport>

    <div class="max-w-3xl mx-auto px-8 py-10">

    <!-- Step 1: Source -->
    <div v-if="step === 0">
      <h2 class="text-xl font-bold mb-2 text-ink-primary">Where is your data?</h2>
      <p class="text-ink-muted text-sm mb-8">Choose the system you're migrating from.</p>

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

      <!-- Tally input methods -->
      <div v-if="source === 'tally'" class="space-y-3">
        <p class="text-xs text-gray-400 dark:text-gray-500 mb-1">Not sure which to choose? Use "Upload Tally Export" -- it's the simplest option.</p>

        <!-- Method 1: Upload XML (Recommended) -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'xml' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="tallyMode = 'xml'" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'xml' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'xml'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm">Upload Tally Export</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">Recommended</span>
              </div>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Export your data from TallyPrime as XML files and upload them here. No setup required.</p>
            </div>
          </button>

          <div v-if="tallyMode === 'xml'" class="px-5 pb-5 space-y-4">
            <!-- Instructions -->
            <button @click="showXmlInstructions = !showXmlInstructions" class="flex items-center gap-1.5 text-xs font-medium text-primary-500 hover:text-primary-600">
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              {{ showXmlInstructions ? 'Hide instructions' : 'How do I export from TallyPrime?' }}
            </button>
            <div v-if="showXmlInstructions" class="bg-blue-50 dark:bg-blue-950/50 border border-blue-100 dark:border-blue-900 rounded-lg p-4">
              <ol class="space-y-2 text-xs text-gray-600 dark:text-gray-300">
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">1</span><span>Open your company in TallyPrime</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">2</span><span>Press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Alt+E</kbd> (or click E:Export in the top menu)</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">3</span><span>Select <strong>Masters</strong> then <strong>All Masters</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">4</span><span>Choose format: <strong>XML (Data Interchange)</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">5</span><span>Choose a location to save (e.g. Desktop) and click Export</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">6</span><span>Go back to Gateway of Tally</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">7</span><span>Press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Alt+E</kbd> again</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">8</span><span>Select <strong>Vouchers/Transactions</strong> then <strong>All Vouchers</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">9</span><span>Choose format: <strong>XML (Data Interchange)</strong> and save</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">10</span><span>Upload both files below</span></li>
              </ol>
            </div>

            <!-- Upload zones -->
            <div class="space-y-3">
              <div>
                <label
                  :class="['flex items-center gap-3 p-4 border-2 border-dashed rounded-xl cursor-pointer transition-colors', mastersFile ? 'border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-950/30' : 'border-gray-300 dark:border-gray-700 hover:border-primary-400']"
                >
                  <div class="flex-shrink-0">
                    <svg v-if="mastersFile" class="w-6 h-6 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    <svg v-else class="w-6 h-6 text-gray-300 dark:text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  </div>
                  <div class="flex-1 min-w-0">
                    <p v-if="mastersFile" class="text-sm font-medium text-green-700 dark:text-green-300 truncate">{{ mastersFile.name }}</p>
                    <p v-else class="text-sm text-gray-500 dark:text-gray-400">Drop your <strong>Masters</strong> XML file here or click to browse</p>
                    <p v-if="mastersFile" class="text-xs text-green-600 dark:text-green-400">{{ formatFileSize(mastersFile.size) }}</p>
                  </div>
                  <input type="file" accept=".xml" class="hidden" @change="e => mastersFile = e.target.files?.[0] || null" />
                </label>
              </div>
              <div>
                <label
                  :class="['flex items-center gap-3 p-4 border-2 border-dashed rounded-xl cursor-pointer transition-colors', vouchersFile ? 'border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-950/30' : 'border-gray-300 dark:border-gray-700 hover:border-primary-400']"
                >
                  <div class="flex-shrink-0">
                    <svg v-if="vouchersFile" class="w-6 h-6 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                    <svg v-else class="w-6 h-6 text-gray-300 dark:text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  </div>
                  <div class="flex-1 min-w-0">
                    <p v-if="vouchersFile" class="text-sm font-medium text-green-700 dark:text-green-300 truncate">{{ vouchersFile.name }}</p>
                    <p v-else class="text-sm text-gray-500 dark:text-gray-400">Drop your <strong>Vouchers</strong> XML file here or click to browse</p>
                    <p v-if="vouchersFile" class="text-xs text-green-600 dark:text-green-400">{{ formatFileSize(vouchersFile.size) }}</p>
                  </div>
                  <input type="file" accept=".xml" class="hidden" @change="e => vouchersFile = e.target.files?.[0] || null" />
                </label>
                <p class="text-[11px] text-gray-400 dark:text-gray-500 mt-1.5 ml-1">Vouchers file is optional -- you can migrate master data first and add transactions later.</p>
              </div>
            </div>
          </div>
        </div>

        <!-- Method 2: Live Server -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'live' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="tallyMode = 'live'" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'live' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'live'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <span class="font-semibold text-sm">Live Server Connection</span>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Connect directly to TallyPrime running on your computer or network. Real-time data pull.</p>
            </div>
          </button>

          <div v-if="tallyMode === 'live'" class="px-5 pb-5 space-y-4">
            <!-- Instructions -->
            <button @click="showLiveInstructions = !showLiveInstructions" class="flex items-center gap-1.5 text-xs font-medium text-primary-500 hover:text-primary-600">
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              {{ showLiveInstructions ? 'Hide instructions' : 'How do I set this up?' }}
            </button>
            <div v-if="showLiveInstructions" class="bg-blue-50 dark:bg-blue-950/50 border border-blue-100 dark:border-blue-900 rounded-lg p-4">
              <ol class="space-y-2 text-xs text-gray-600 dark:text-gray-300">
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">1</span><span>Open TallyPrime with your company loaded</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">2</span><span>Press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">F1</kbd> (Help) then go to <strong>Settings</strong> then <strong>Connectivity</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">3</span><span>Set <strong>"TallyPrime acts as"</strong> to <strong>"Both"</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">4</span><span>Note the <strong>Port number</strong> (default: 9000)</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">5</span><span>Press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Ctrl+A</kbd> to save</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">6</span><span>Find your IP address: press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Win+R</kbd>, type <code class="font-mono text-[10px]">cmd</code>, press Enter, then type <code class="font-mono text-[10px]">ipconfig</code></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">7</span><span>Look for <strong>"IPv4 Address"</strong> (e.g. 192.168.1.100) and enter it below</span></li>
              </ol>
            </div>

            <!-- Connection inputs -->
            <div class="flex gap-3">
              <div class="flex-1">
                <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">TallyPrime IP Address</label>
                <input
                  v-model="tallyHost"
                  type="text"
                  placeholder="e.g. 192.168.1.100"
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

            <!-- Troubleshooting tips on error -->
            <div v-if="connectionStatus === 'error'" class="bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
              <p class="text-xs font-medium text-amber-700 dark:text-amber-300 mb-2">Troubleshooting:</p>
              <ul class="space-y-1 text-xs text-amber-600 dark:text-amber-400">
                <li class="flex gap-1.5"><span>-</span><span>Make sure TallyPrime is open with your company loaded</span></li>
                <li class="flex gap-1.5"><span>-</span><span>Check that "TallyPrime acts as" is set to "Both" in settings</span></li>
                <li class="flex gap-1.5"><span>-</span><span>Make sure no firewall is blocking port {{ tallyPort }}</span></li>
                <li class="flex gap-1.5"><span>-</span><span>If TallyPrime is on a different computer, both must be on the same network</span></li>
              </ul>
            </div>
          </div>
        </div>

        <!-- Method 3: Upload Excel -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'excel' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="tallyMode = 'excel'" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'excel' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'excel'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <span class="font-semibold text-sm">Upload Excel Exports</span>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Export individual reports from TallyPrime as Excel files. Good if XML export isn't available.</p>
            </div>
          </button>

          <div v-if="tallyMode === 'excel'" class="px-5 pb-5 space-y-4">
            <!-- Instructions -->
            <button @click="showExcelInstructions = !showExcelInstructions" class="flex items-center gap-1.5 text-xs font-medium text-primary-500 hover:text-primary-600">
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              {{ showExcelInstructions ? 'Hide instructions' : 'How do I export Excel files?' }}
            </button>
            <div v-if="showExcelInstructions" class="bg-blue-50 dark:bg-blue-950/50 border border-blue-100 dark:border-blue-900 rounded-lg p-4">
              <ol class="space-y-2 text-xs text-gray-600 dark:text-gray-300">
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">1</span><span>Open your company in TallyPrime</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">2</span><span>Go to <strong>Chart of Accounts</strong></span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">3</span><span>Press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Ctrl+E</kbd> and choose Format: <strong>Excel</strong>, then Save</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">4</span><span>Go to Gateway then <strong>Stock Summary</strong>, press <kbd class="px-1 py-0.5 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 font-mono text-[10px]">Ctrl+E</kbd> and Save as Excel</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">5</span><span>Go to <strong>Display More Reports</strong> then <strong>Trial Balance</strong>, export as Excel</span></li>
                <li class="flex gap-2"><span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 flex items-center justify-center text-[10px] font-bold">6</span><span>Upload the files below</span></li>
              </ol>
            </div>

            <!-- File uploads -->
            <div class="space-y-2">
              <FileUploadRow label="Chart of Accounts" accept=".xlsx,.xls" required :file="excelCoA" @change="excelCoA = $event" />
              <FileUploadRow label="Stock Summary" accept=".xlsx,.xls" :file="excelStock" @change="excelStock = $event" />
              <FileUploadRow label="Trial Balance" accept=".xlsx,.xls" :file="excelTrial" @change="excelTrial = $event" />
              <FileUploadRow label="Day Book" accept=".xlsx,.xls" :file="excelDayBook" @change="excelDayBook = $event" />
            </div>
            <p class="text-[11px] text-gray-400 dark:text-gray-500 ml-1">Upload at least the Chart of Accounts. Other files help with a more complete migration.</p>
          </div>
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
        <p v-if="connectionCompany" class="text-xs text-primary-600 flex items-center gap-1.5 -mt-1 mb-1">
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
          Detected from your Tally data. You can edit if needed.
        </p>
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

    </div><!-- /max-w-3xl wrapper -->
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import StepIndicator from '@/components/StepIndicator.vue'
import SourceCard from '@/components/SourceCard.vue'
import FileUploadRow from '@/components/FileUploadRow.vue'
import { testTallyConnection, fetchTallyData, executeMigration } from '@/services/api.js'

const stepLabels = ['Source', 'Target', 'Preview', 'Migrate', 'Validate']
const step = ref(0)
const headerMounted = ref(false)
onMounted(() => { headerMounted.value = !!document.querySelector('header .flex-1') })

// Step 1: Source
const source = ref('')
const tallyMode = ref('xml')

// XML upload mode
const mastersFile = ref(null)
const vouchersFile = ref(null)
const showXmlInstructions = ref(false)

// Live server mode
const tallyHost = ref('')
const tallyPort = ref(9000)
const testingConnection = ref(false)
const connectionStatus = ref('')
const connectionCompany = ref('')
const connectionError = ref('')
const showLiveInstructions = ref(false)

// Excel upload mode
const excelCoA = ref(null)
const excelStock = ref(null)
const excelTrial = ref(null)
const excelDayBook = ref(null)
const showExcelInstructions = ref(false)

const canProceedFromSource = computed(() => {
  if (source.value !== 'tally') return false
  if (tallyMode.value === 'xml') return !!mastersFile.value
  if (tallyMode.value === 'live') return connectionStatus.value === 'ok'
  if (tallyMode.value === 'excel') return !!excelCoA.value
  return false
})

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

async function testConnection() {
  testingConnection.value = true
  connectionStatus.value = ''
  connectionError.value = ''
  try {
    const result = await testTallyConnection(tallyHost.value, tallyPort.value)
    if (result.connected) {
      connectionStatus.value = 'ok'
      connectionCompany.value = result.company || 'Connected'
    } else {
      connectionStatus.value = 'error'
      connectionError.value = result.error || 'Could not connect to TallyPrime'
    }
  } catch (e) {
    connectionStatus.value = 'error'
    connectionError.value = e.message || 'Connection failed'
  } finally {
    testingConnection.value = false
  }
}

// Step 2: Target
const target = ref('')
const companyName = ref('')
const companyAbbr = ref('')
const fetchingPreview = ref(false)

function stripYearSuffix(name) {
  return name.replace(/\s*-\s*\d{4}-?\d{0,2}\s*$/, '').trim()
}

function generateAbbr(name) {
  const words = name.replace(/[-&]/g, ' ').split(/\s+/).filter(w => w.length > 0)
  return words.map(w => w[0]).join('').toUpperCase().slice(0, 4)
}

// Auto-fill company fields when Tally company is detected
watch(step, (newStep) => {
  if (newStep === 1 && connectionCompany.value && !companyName.value) {
    const cleaned = stripYearSuffix(connectionCompany.value)
    companyName.value = cleaned
    companyAbbr.value = generateAbbr(cleaned)
  }
})

const canProceedFromTarget = computed(() => {
  return target.value === 'erpnext' && companyName.value.trim() && companyAbbr.value.trim()
})

async function fetchPreview() {
  fetchingPreview.value = true
  try {
    const result = await fetchTallyData(tallyHost.value, tallyPort.value)
    previewEntities.value = [
      { key: 'groups', label: 'Account Groups', count: result.groups_count || 0, enabled: true, expanded: false,
        samples: (result.sample_accounts || []).slice(0, 5) },
      { key: 'ledgers', label: 'Ledger Accounts', count: result.ledgers_count || 0, enabled: true, expanded: false,
        samples: (result.sample_accounts || []).slice(0, 5) },
      { key: 'customers', label: 'Customers', count: result.customers_count || 0, enabled: true, expanded: false,
        samples: (result.sample_customers || []).slice(0, 5) },
      { key: 'suppliers', label: 'Suppliers', count: result.suppliers_count || 0, enabled: true, expanded: false,
        samples: (result.sample_suppliers || []).slice(0, 5) },
      { key: 'items', label: 'Stock Items', count: result.stock_items_count || 0, enabled: true, expanded: false,
        samples: (result.sample_stock || []).slice(0, 5) },
    ]
    step.value = 2
  } catch (e) {
    alert('Failed to fetch data: ' + (e.message || e))
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
    { label: 'Connecting to Tally and importing data', status: 'running', detail: '' },
  ]
  migrationLogs.value = [
    { time: new Date().toLocaleTimeString(), message: 'Starting migration...', level: 'info' },
  ]

  try {
    const result = await executeMigration(
      tallyHost.value, tallyPort.value,
      companyName.value, companyAbbr.value,
      false
    )

    migrationTasks.value[0].status = 'done'
    migrationLogs.value.push(
      { time: new Date().toLocaleTimeString(), message: `Migration complete`, level: 'success' }
    )

    // Build validation stats from result
    validationStats.value = []
    if (result.created != null) validationStats.value.push({ label: 'Created', value: result.created })
    if (result.skipped != null) validationStats.value.push({ label: 'Skipped', value: result.skipped })
    if (result.errors != null) validationStats.value.push({ label: 'Errors', value: result.errors })

    // Show error details if any
    if (result.error_details && result.error_details.length) {
      validationErrors.value = result.error_details.map(e => typeof e === 'string' ? e : JSON.stringify(e))
    }

    step.value = 4
  } catch (e) {
    migrationTasks.value[0].status = 'error'
    migrationTasks.value[0].detail = e.message || 'Migration failed'
    migrationLogs.value.push(
      { time: new Date().toLocaleTimeString(), message: `Error: ${e.message || e}`, level: 'error' }
    )
  }
}

// Step 5: Validation
const validationStats = ref([])
const validationErrors = ref([])

function goToStep(i) {
  if (i <= step.value) step.value = i
}
</script>
