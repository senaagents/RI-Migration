<template>
  <div>
    <!-- Header stepper -->
    <Teleport to="header .flex-1" v-if="headerMounted">
      <StepIndicator :steps="stepLabels" :currentStep="step" @go="goToStep" />
    </Teleport>

    <div :class="[step === 2 ? 'max-w-7xl' : 'max-w-3xl', 'mx-auto px-8 py-10']">

    <!-- Step 1: Source -->
    <div v-if="step === 0">
      <h2 class="text-xl font-bold mb-2 text-ink-primary">Source</h2>
      <p class="text-ink-muted text-sm mb-8">Connect a source, capture a snapshot, and normalize enough for preview.</p>

      <div v-if="loadingSourceTypes" class="text-center py-8 text-ink-faint text-sm">Loading source types...</div>
      <div v-else-if="sourceTypesError" class="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 mb-8">
        <p class="font-semibold text-sm text-red-700 dark:text-red-300">Could not load source types</p>
        <p class="text-xs text-red-600 dark:text-red-400 mt-1">{{ sourceTypesError }}</p>
        <button
          @click="loadSourceTypes"
          class="mt-4 px-4 py-2 bg-white dark:bg-gray-900 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 rounded-lg text-sm font-medium hover:bg-red-100 dark:hover:bg-red-950 transition-colors"
        >Retry</button>
      </div>
      <div v-else-if="!sourceTypes.length" class="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 mb-8">
        <p class="font-semibold text-sm text-ink-primary">No source types installed</p>
        <p class="text-xs text-ink-muted mt-1">Run the Migration app install or migrate step to seed Tally, SAP, and CSV / Excel source types.</p>
      </div>
      <div v-else class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
        <SourceCard
          v-for="sourceType in sourceTypes"
          :key="sourceType.source_key"
          :title="sourceType.title"
          :subtitle="sourceType.subtitle"
          :selected="source === sourceType.source_key"
          @select="selectSource(sourceType)"
          :iconBg="sourceType.icon_bg_class || 'bg-gray-100 dark:bg-gray-800'"
        >
          <template #icon>
            <span :class="['text-sm font-bold', sourceType.icon_text_class || 'text-gray-500']">{{ sourceType.icon_label || sourceType.title?.slice(0, 2) }}</span>
          </template>
        </SourceCard>
      </div>

      <!-- Tally input methods -->
      <div v-if="source === 'tally'" class="space-y-3">
        <p class="text-xs text-gray-400 dark:text-gray-500 mb-1">Use the bridge first. Direct Tally is available when this backend can reach the Tally machine. XML upload stays as the offline fallback.</p>

        <!-- Method 1: Sena Bridge (Recommended) -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'bridge' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="selectTallyMode('bridge')" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'bridge' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'bridge'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm">Sena Tally Bridge</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">Recommended</span>
              </div>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Install a small Windows bridge beside Tally. It keeps a read-only analytics copy fresh without exposing your Tally port.</p>
            </div>
          </button>

          <div v-if="tallyMode === 'bridge'" class="px-5 pb-5 space-y-4">
            <div class="bg-blue-50 dark:bg-blue-950/50 border border-blue-100 dark:border-blue-900 rounded-lg p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p class="text-sm font-semibold text-ink-primary">1. Download on the Tally Windows computer</p>
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Unzip it, keep Tally open, and leave the company loaded.</p>
              </div>
              <a
                href="/bridge/sena_tally_bridge.zip"
                download
                class="inline-flex justify-center px-4 py-2 bg-white dark:bg-gray-900 border border-blue-100 dark:border-blue-900 rounded-lg text-sm font-medium text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-950 transition-colors"
              >Download bridge ZIP</a>
            </div>

            <div class="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3">
              <div>
                <p class="text-sm font-semibold text-ink-primary">2. Create pairing code</p>
                <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">This creates a pending bridge pairing. It is not connected until the Windows bridge claims it.</p>
              </div>
              <button
                @click="createBridgePairing"
                :disabled="creatingPairing"
                class="w-full sm:w-auto px-4 py-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 rounded-lg text-sm font-medium hover:bg-gray-700 dark:hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {{ creatingPairing ? 'Creating...' : 'Create New Pairing Code' }}
              </button>
              <button
                v-if="bridgePairing?.pairing_code"
                @click="copyToClipboard(bridgePairing.pairing_code, 'pairing')"
                class="w-full rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left hover:border-primary-300 transition-colors"
              >
                <span class="text-xs text-gray-400 mr-2">Pairing code</span>
                <span class="font-mono font-bold tracking-wider">{{ bridgePairing.pairing_code }}</span>
                <span class="ml-3 text-xs font-medium text-primary-500">{{ copiedTarget === 'pairing' ? 'Copied' : 'Click to copy' }}</span>
              </button>
              <p v-if="bridgePairing?.pairing_code" class="text-xs text-gray-400 dark:text-gray-500">Connection record: {{ bridgePairing.connection_id }}.</p>
              <p v-else-if="bridgePairing" class="text-xs text-gray-400 dark:text-gray-500">Connection record: {{ bridgePairing.connection_id }}. This saved source can move forward or be refreshed after the bridge syncs again.</p>
              <p v-if="bridgeError" class="text-xs text-red-500">{{ bridgeError }}</p>
            </div>

            <div v-if="bridgePairing?.pairing_code" class="rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 p-4 space-y-3">
              <div>
                <p class="text-sm font-semibold text-ink-primary">3. Run this in Windows PowerShell</p>
                <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Use the unzipped bridge folder on the Tally computer.</p>
              </div>
              <div class="relative">
                <button
                  @click="copyToClipboard(bridgePowerShellCommand, 'bridge-command')"
                  class="absolute right-2 top-2 px-2 py-1 rounded bg-white/10 text-gray-100 text-[10px] font-medium hover:bg-white/20"
                >{{ copiedTarget === 'bridge-command' ? 'Copied' : 'Copy command' }}</button>
                <pre class="overflow-x-auto rounded-lg bg-gray-900 text-gray-100 p-3 pr-28 text-[11px] leading-relaxed"><code>{{ bridgePowerShellCommand }}</code></pre>
              </div>
              <p class="text-xs text-gray-400 dark:text-gray-500">After it finishes, click Refresh discovery here. If Tally is running on another Windows host, replace <code class="font-mono text-[10px]">localhost</code> with that machine's Tally IP.</p>
            </div>

            <div v-if="bridgePairing" class="rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
              <div class="px-4 py-3 flex items-center gap-3 bg-gray-50 dark:bg-gray-950">
                <div class="flex-1 min-w-0">
                  <p class="font-semibold text-sm text-ink-primary">4. Refresh discovery</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ bridgeDiscoverySubtitle }}</p>
                </div>
                <button
                  @click="refreshTallyDiscovery"
                  :disabled="loadingBridgeDiscovery"
                  class="px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 hover:border-primary-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >{{ loadingBridgeDiscovery ? 'Refreshing...' : 'Refresh discovery' }}</button>
              </div>
              <div v-if="bridgeAutoRefreshActive" class="px-4 py-2 text-xs text-blue-600 dark:text-blue-300 border-t border-gray-200 dark:border-gray-800 bg-blue-50 dark:bg-blue-950/30">
                Waiting for the Windows bridge. Checking automatically every 5 seconds.
              </div>
              <div v-if="bridgeDiscoveryError" class="px-4 py-3 text-xs text-red-500 border-t border-gray-200 dark:border-gray-800">{{ bridgeDiscoveryError }}</div>
              <div v-else-if="!tallyDiscoveryItems.length" class="px-4 py-4 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-800">
                Run the bridge in Windows, then refresh discovery to choose which Tally data should be snapped and normalized for preview.
              </div>
              <div v-else class="divide-y divide-gray-100 dark:divide-gray-800">
                <label
                  v-for="item in tallyDiscoveryItems"
                  :key="item.key"
                  class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-950 transition-colors"
                >
                  <input
                    type="checkbox"
                    v-model="item.enabled"
                    class="w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-500"
                  />
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-ink-primary">{{ item.label }}</p>
                    <p class="text-xs text-gray-400 dark:text-gray-500">{{ item.detail }}</p>
                  </div>
                  <span class="text-xs font-mono text-gray-400">{{ item.count.toLocaleString() }}</span>
                </label>
              </div>
            </div>

            <details class="rounded-lg border border-gray-200 dark:border-gray-800 overflow-hidden">
              <summary class="px-4 py-3 cursor-pointer list-none bg-gray-50 dark:bg-gray-950">
                <div class="flex items-center gap-3">
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-semibold text-ink-primary">Use an existing bridge</p>
                    <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">For a bridge that was already paired on this site.</p>
                  </div>
                  <span class="text-xs text-gray-400 dark:text-gray-500">Optional</span>
                </div>
              </summary>
              <div class="border-t border-gray-200 dark:border-gray-800">
                <div class="px-4 py-3 flex items-center justify-between gap-3">
                  <p class="text-xs text-gray-400 dark:text-gray-500">Choose an active or previously paired source.</p>
                  <button
                    @click="loadTallyConnections"
                    :disabled="loadingTallyConnections"
                    class="px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 hover:border-primary-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >{{ loadingTallyConnections ? 'Loading...' : 'Reload' }}</button>
                </div>
                <div v-if="tallyConnectionsError" class="px-4 py-3 text-xs text-red-500 border-t border-gray-200 dark:border-gray-800">{{ tallyConnectionsError }}</div>
                <div v-else-if="tallyConnectionsNotice" class="px-4 py-3 text-xs text-green-600 dark:text-green-400 border-t border-gray-200 dark:border-gray-800">{{ tallyConnectionsNotice }}</div>
                <div v-else-if="loadingTallyConnections" class="px-4 py-4 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-800">
                  Loading saved bridges...
                </div>
                <div v-else-if="!tallyConnections.length" class="px-4 py-4 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-800">
                  No existing bridges found.
                </div>
                <div v-else class="divide-y divide-gray-100 dark:divide-gray-800">
                  <div
                    v-for="connection in tallyConnections"
                    :key="connection.name"
                    :class="['px-4 py-3 flex items-center gap-3', selectedBridgeConnectionId === connection.name ? 'bg-primary-50 dark:bg-primary-950/30' : 'bg-white dark:bg-gray-900']"
                  >
                    <div class="flex-1 min-w-0">
                      <div class="flex items-center gap-2">
                        <p class="text-sm font-medium text-ink-primary truncate">{{ connection.tally_company_name || connection.connection_label || connection.name }}</p>
                        <span :class="['text-[10px] font-bold px-2 py-0.5 rounded-full', connection.status === 'Active' ? 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300' : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400']">{{ connection.status }}</span>
                      </div>
                      <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                        {{ connection.name }}<span v-if="connection.last_sync_at"> · synced {{ formatDateTime(connection.last_sync_at) }}</span><span v-else-if="connection.last_seen_at"> · seen {{ formatDateTime(connection.last_seen_at) }}</span>
                      </p>
                    </div>
                    <button
                      @click="resumeTallyConnection(connection)"
                      :disabled="loadingBridgeDiscovery && selectedBridgeConnectionId === connection.name"
                      class="px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 hover:border-primary-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >{{ selectedBridgeConnectionId === connection.name ? 'Using' : 'Use' }}</button>
                  </div>
                </div>
              </div>
            </details>
          </div>
        </div>

        <!-- Method 2: Upload XML -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'xml' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="selectTallyMode('xml')" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'xml' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'xml'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm">Upload Tally Export</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">Fallback</span>
              </div>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Export your data from TallyPrime as XML files. Upload processing is the offline fallback path we will wire next.</p>
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
            <div class="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 p-3">
              <p class="text-xs text-amber-700 dark:text-amber-300">XML upload is visible as the offline fallback, but import from uploaded files is not enabled yet. Use the bridge or direct Tally connection for this run.</p>
            </div>
          </div>
        </div>

        <!-- Method 3: Direct Tally Connection -->
        <div
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'live' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="selectTallyMode('live')" class="w-full text-left px-5 py-4 flex items-start gap-3">
            <div class="flex-shrink-0 mt-0.5">
              <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center', tallyMode === 'live' ? 'border-primary-500' : 'border-gray-300 dark:border-gray-600']">
                <div v-if="tallyMode === 'live'" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm">Direct Tally Connection</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">Admin fallback</span>
              </div>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Connect directly to TallyPrime over the local network when this backend can reach the Tally HTTP port.</p>
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

        <!-- Method 4: Upload Excel -->
        <div
          v-if="false"
          :class="['rounded-xl border-2 transition-all overflow-hidden', tallyMode === 'excel' ? 'border-primary-500 bg-white dark:bg-gray-900' : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900']"
        >
          <button @click="selectTallyMode('excel')" class="w-full text-left px-5 py-4 flex items-start gap-3">
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

      <div v-if="source === 'sap'" class="space-y-4">
        <div class="rounded-xl border-2 border-primary-500 bg-white dark:bg-gray-900 overflow-hidden">
          <div class="px-5 py-4">
            <div class="flex items-center gap-2 mb-1">
              <span class="font-semibold text-sm">SAP Connection</span>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">HANA live</span>
            </div>
            <p class="text-xs text-gray-400 dark:text-gray-500">Use the saved read-only HANA connection, refresh table discovery, then extract master data for preview before any ERP write.</p>
          </div>
          <div class="px-5 pb-5 space-y-3">
            <div>
              <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Connection label</label>
              <input
                v-model="sapConnectionLabel"
                type="text"
                placeholder="e.g. RFGB SAP B1"
                class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
            </div>
            <div class="rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
              <div class="px-4 py-3 flex items-center gap-3 bg-gray-50 dark:bg-gray-950">
                <div class="flex-1 min-w-0">
                  <p class="font-semibold text-sm text-ink-primary">Saved SAP sources</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Select the LLM HANA snapshot or refresh discovery from the configured local credentials.</p>
                </div>
                <button
                  @click="loadSapConnections"
                  :disabled="loadingSapConnections"
                  class="px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 hover:border-primary-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >{{ loadingSapConnections ? 'Loading...' : 'Reload' }}</button>
              </div>
              <div v-if="sapError" class="px-4 py-3 text-xs text-red-500 border-t border-gray-200 dark:border-gray-800">{{ sapError }}</div>
              <div v-else-if="sapNotice" class="px-4 py-3 text-xs text-green-600 dark:text-green-400 border-t border-gray-200 dark:border-gray-800">{{ sapNotice }}</div>
              <div v-else-if="loadingSapConnections" class="px-4 py-4 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-800">Loading saved SAP sources...</div>
              <div v-else-if="!sapConnections.length" class="px-4 py-4 text-xs text-gray-400 dark:text-gray-500 border-t border-gray-200 dark:border-gray-800">No SAP source snapshot yet. Create one from HANA.</div>
              <div v-else class="divide-y divide-gray-100 dark:divide-gray-800">
                <div
                  v-for="connection in sapConnections"
                  :key="connection.name"
                  :class="['px-4 py-3 flex items-center gap-3', selectedSapConnectionId === connection.name ? 'bg-primary-50 dark:bg-primary-950/30' : 'bg-white dark:bg-gray-900']"
                >
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2">
                      <p class="text-sm font-medium text-ink-primary truncate">{{ connection.connection_label || connection.name }}</p>
                      <span :class="['text-[10px] font-bold px-2 py-0.5 rounded-full', connection.status === 'Active' ? 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300' : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400']">{{ connection.status }}</span>
                    </div>
                    <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      {{ connection.name }}<span v-if="connection.last_sync_at"> · synced {{ formatDateTime(connection.last_sync_at) }}</span>
                    </p>
                  </div>
                  <button
                    @click="resumeSapConnection(connection)"
                    class="px-3 py-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-medium text-gray-700 dark:text-gray-200 hover:border-primary-300 transition-colors"
                  >{{ selectedSapConnectionId === connection.name ? 'Selected' : 'Use' }}</button>
                </div>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <button
                @click="createSapSnapshot"
                :disabled="creatingSapSnapshot"
                class="px-4 py-2 bg-gray-900 dark:bg-white text-white dark:text-gray-900 rounded-lg text-sm font-medium hover:bg-gray-700 dark:hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >{{ creatingSapSnapshot ? 'Refreshing...' : 'Refresh HANA Discovery' }}</button>
              <button
                @click="extractSapMasters"
                :disabled="extractingSapMasters || !selectedSapConnectionId"
                class="px-4 py-2 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >{{ extractingSapMasters ? 'Extracting...' : 'Extract Master Data' }}</button>
            </div>
            <div v-if="sapDiscoveryItems.length" class="rounded-xl border border-gray-200 dark:border-gray-800 divide-y divide-gray-100 dark:divide-gray-800">
              <label
                v-for="item in sapDiscoveryItems"
                :key="item.key"
                class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-950 transition-colors"
              >
                <input
                  type="checkbox"
                  v-model="item.enabled"
                  class="w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-500"
                />
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-medium text-ink-primary">{{ item.label }}</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500">{{ item.detail }}</p>
                </div>
                <span class="text-xs font-mono text-gray-400">{{ item.count.toLocaleString() }}</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      <div v-if="source === 'excel'" class="space-y-4">
        <div class="rounded-xl border-2 border-primary-500 bg-white dark:bg-gray-900 overflow-hidden">
          <div class="px-5 py-4">
            <div class="flex items-center gap-2 mb-1">
              <span class="font-semibold text-sm">CSV / Excel Upload</span>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300">Mapping next</span>
            </div>
            <p class="text-xs text-gray-400 dark:text-gray-500">Upload spreadsheets as a source connection, then normalize sheets and columns for preview or target mapping.</p>
          </div>
          <div class="px-5 pb-5 space-y-3">
            <FileUploadRow label="Workbook or CSV" accept=".xlsx,.xls,.csv" required :file="excelWorkbook" @change="excelWorkbook = $event" />
            <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-4 py-3">
              <p class="font-medium text-sm">Planned discovery</p>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Sheets, headers, inferred field types, sample rows, mapping confidence.</p>
            </div>
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
      <h2 class="text-xl font-bold mb-2 text-ink-primary">Where should the data go?</h2>
      <p class="text-ink-muted text-sm mb-8">Choose an installed agent app. Migrate starts only after the target is selected.</p>

      <div v-if="loadingTargetTypes" class="text-center py-8 text-ink-faint text-sm">Loading migration targets...</div>
      <div v-else-if="targetTypesError" class="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 mb-8">
        <p class="font-semibold text-sm text-red-700 dark:text-red-300">Could not load migration targets</p>
        <p class="text-xs text-red-600 dark:text-red-400 mt-1">{{ targetTypesError }}</p>
        <button
          @click="loadTargetTypes"
          class="mt-4 px-4 py-2 bg-white dark:bg-gray-900 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 rounded-lg text-sm font-medium hover:bg-red-100 dark:hover:bg-red-950 transition-colors"
        >Retry</button>
      </div>
      <div v-else-if="!targetTypes.length" class="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 mb-8">
        <p class="font-semibold text-sm text-ink-primary">No migration targets installed</p>
        <p class="text-xs text-ink-muted mt-1">Install an agent app that declares migration target capability.</p>
      </div>
      <div v-else class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
        <SourceCard
          v-for="targetType in targetTypes"
          :key="targetType.target_key"
          :title="targetType.title"
          :subtitle="targetType.subtitle"
          :selected="target === targetType.target_key"
          :disabled="targetType.implementation_status !== 'Ready'"
          :disabledLabel="targetType.implementation_status || 'Not Ready'"
          @select="selectTarget(targetType.target_key)"
          :iconBg="targetType.icon_bg_class || 'bg-gray-100 dark:bg-gray-800'"
        >
          <template #icon>
            <span :class="['text-sm font-bold', targetType.icon_text_class || 'text-gray-500']">{{ targetType.icon_label || targetType.title?.slice(0, 2) }}</span>
          </template>
        </SourceCard>
      </div>

      <!-- Company selection -->
      <div v-if="target === 'sena_erp'">
        <div v-if="loadingCompanies" class="text-center py-8 text-ink-faint text-sm">Loading companies...</div>

        <div v-else-if="!companies.length" class="bg-white rounded-xl border border-gray-200 p-6 text-center">
          <p class="text-ink-muted text-sm">No companies found. Please set up a company in SenaERP first.</p>
        </div>

        <div v-else class="space-y-3">
          <p class="text-xs font-medium text-ink-muted mb-2">Select the company to import into</p>
          <button
            v-for="co in companies"
            :key="co.name"
            @click="selectedCompany = co"
            :class="[
              'w-full text-left rounded-xl border-2 p-4 transition-all flex items-center gap-4',
              selectedCompany?.name === co.name
                ? 'border-primary-500 bg-primary-50'
                : 'border-gray-200 bg-white hover:border-primary-300'
            ]"
          >
            <div :class="['w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0', selectedCompany?.name === co.name ? 'border-primary-500' : 'border-gray-300']">
              <div v-if="selectedCompany?.name === co.name" class="w-2.5 h-2.5 rounded-full bg-primary-500" />
            </div>
            <div class="flex-1 min-w-0">
              <p class="font-semibold text-sm text-ink-primary">{{ co.name }}</p>
              <p class="text-xs text-ink-muted mt-0.5">{{ co.abbr }} &middot; {{ co.default_currency }} &middot; {{ co.country }}</p>
            </div>
          </button>

          <!-- Warning -->
          <div v-if="selectedCompany" class="bg-amber-50 border border-amber-200 rounded-lg p-3 mt-4 flex items-start gap-2">
            <svg class="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <p class="text-xs text-amber-700">Data from {{ sourceTitle }} will be imported into <strong>{{ selectedCompany.name }}</strong>. This may modify existing accounts, add customers, suppliers, and items.</p>
          </div>
        </div>
      </div>

      <div v-if="loadingTargetSchema" class="bg-white rounded-xl border border-gray-200 p-5">
        <p class="text-sm text-ink-muted">Discovering target schema...</p>
      </div>
      <div v-else-if="targetSchemaError" class="rounded-xl border border-amber-200 bg-amber-50 p-5">
        <p class="font-semibold text-sm text-amber-800">Target schema unavailable</p>
        <p class="text-xs text-amber-700 mt-1">{{ targetSchemaError }}</p>
      </div>
      <div v-else-if="selectedTargetSchema" class="rounded-xl border border-gray-200 bg-white p-5">
        <p class="font-semibold text-sm text-ink-primary">Target schema discovered</p>
        <p class="text-xs text-ink-muted mt-1">
          {{ selectedTargetSchema.schema.doctype_count }} DocTypes · {{ selectedTargetSchema.schema.field_count }} fields ·
          {{ selectedTargetSchema.schema.writable_field_count }} writable · {{ selectedTargetSchema.schema.skipped_field_count }} skipped
        </p>
        <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
          <div
            v-for="doctypeSchema in selectedTargetSchema.schema.doctypes.slice(0, 4)"
            :key="doctypeSchema.doctype"
            class="rounded-lg border border-gray-200 px-3 py-2"
          >
            <p class="text-sm font-medium text-ink-primary">{{ doctypeSchema.doctype }}</p>
            <p class="text-xs text-ink-muted mt-0.5">
              {{ doctypeSchema.field_count }} fields · {{ doctypeSchema.writable_field_count }} writable · {{ doctypeSchema.skipped_field_count }} skipped
            </p>
          </div>
        </div>
        <p v-if="selectedTargetSchema.schema.doctypes.length > 4" class="text-[11px] text-ink-muted mt-2">
          + {{ selectedTargetSchema.schema.doctypes.length - 4 }} more DocTypes
        </p>
      </div>

      <div class="flex justify-between mt-8">
        <button @click="step = 0" class="px-6 py-2.5 text-sm font-medium text-ink-muted hover:text-ink-primary">Back</button>
        <button
          @click="fetchPreview"
          :disabled="!canProceedFromTarget"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >{{ fetchingPreview ? 'Loading Mapping...' : 'Continue to Mapping' }}</button>
      </div>
    </div>

    <!-- Step 3: Mapping -->
    <div v-if="step === 2">
      <h2 class="text-xl font-bold mb-1">Mapping</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">
        Match the source output contract to the target input contract. The middle column is the first deterministic mapping plan; AI suggestions can sit here after this contract shape is stable.
      </p>

      <div class="grid grid-cols-1 xl:grid-cols-[1fr_1.15fr_1fr] gap-4">
        <section class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
          <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
            <p class="text-sm font-semibold text-ink-primary">Source Output</p>
            <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ previewEntities.length }} record groups from {{ sourceLabel }}</p>
          </div>
          <div class="divide-y divide-gray-100 dark:divide-gray-800 max-h-[620px] overflow-y-auto">
            <div
              v-for="entity in previewEntities"
              :key="entity.key"
              class="px-4 py-3"
            >
              <div class="flex items-start gap-3">
                <input
                  type="checkbox"
                  v-model="entity.enabled"
                  class="mt-1 w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-500"
                />
                <div class="flex-1 min-w-0">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                      <p class="font-medium text-sm text-ink-primary truncate">{{ entity.label }}</p>
                      <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                        {{ formatNumber(entity.count) }} records · {{ sourceFieldCount(entity) }} fields
                      </p>
                    </div>
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
                  <div v-if="entity.fields?.length" class="flex flex-wrap gap-1.5 mt-3">
                    <span
                      v-for="(field, fi) in entity.fields.slice(0, 8)"
                      :key="`${sourceFieldName(field)}-${fi}`"
                      class="px-2 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-[11px] text-gray-600 dark:text-gray-300"
                    >{{ sourceFieldName(field) }}</span>
                    <span v-if="entity.fields.length > 8" class="px-2 py-1 rounded-md bg-gray-50 dark:bg-gray-950 text-[11px] text-gray-400">
                      +{{ entity.fields.length - 8 }}
                    </span>
                  </div>
                  <div v-if="entity.expanded && entity.samples?.length" class="mt-3 rounded-lg border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 overflow-hidden">
                    <div
                      v-for="(sample, si) in entity.samples.slice(0, 4)"
                      :key="si"
                      class="grid grid-cols-[auto_1fr] gap-x-3 px-3 py-2 text-xs border-b border-gray-100 dark:border-gray-800 last:border-0"
                    >
                      <span class="text-gray-400 font-mono">{{ si + 1 }}</span>
                      <span class="min-w-0">
                        <span class="block font-medium text-ink-primary truncate">{{ sample.name || sample.record_name || sample.source_id || sample }}</span>
                        <span class="block text-gray-400 truncate">{{ sample.detail || sample.parent || sample.source_id || '' }}</span>
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
          <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
            <p class="text-sm font-semibold text-ink-primary">Mapping Plan</p>
            <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">Baseline suggestions. Review before any write/import runs.</p>
          </div>
          <div class="divide-y divide-gray-100 dark:divide-gray-800 max-h-[620px] overflow-y-auto">
            <div
              v-for="row in mappingRows"
              :key="row.source.key"
              :class="['px-5 py-4', row.source.enabled ? 'bg-white dark:bg-gray-900' : 'bg-gray-50 dark:bg-gray-950 opacity-60']"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-ink-primary truncate">{{ row.source.label }}</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ formatNumber(row.source.count) }} source rows</p>
                </div>
                <span :class="['text-[10px] font-bold px-2 py-1 rounded-md', row.confidence === 'High' ? 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300' : row.target ? 'bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300' : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400']">
                  {{ row.confidence }}
                </span>
              </div>
              <div class="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-3 py-2">
                  <p class="text-[11px] uppercase tracking-wide text-gray-400">From</p>
                  <p class="text-sm font-medium text-ink-primary truncate">{{ row.source.label }}</p>
                  <p class="text-[11px] text-gray-400 truncate">{{ row.source.objectType || row.source.key }}</p>
                </div>
                <svg class="w-4 h-4 text-gray-300 dark:text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>
                <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-3 py-2">
                  <p class="text-[11px] uppercase tracking-wide text-gray-400">To</p>
                  <p class="text-sm font-medium text-ink-primary truncate">{{ row.target?.doctype || 'Needs target' }}</p>
                  <p class="text-[11px] text-gray-400 truncate">{{ row.target ? 'Sena ERP DocType' : 'No target selected' }}</p>
                </div>
              </div>
              <div v-if="row.target" class="mt-3 flex items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400">
                <span>Target requires {{ row.requiredFields.length }} fields. Writable fields available: {{ row.target.writable_field_count }}.</span>
                <button
                  @click="toggleMappingDetails(row.source.key)"
                  class="px-2.5 py-1 rounded-md border border-gray-200 dark:border-gray-800 text-gray-600 dark:text-gray-300 hover:border-primary-300 transition-colors"
                >{{ mappingDetailsOpen(row.source.key) ? 'Hide details' : 'View details' }}</button>
              </div>
              <div v-else class="mt-3 text-xs text-amber-600 dark:text-amber-300">
                No deterministic target selected yet. This group should be handled by manual mapping or AI-assisted transform rules.
              </div>
              <div v-if="row.target && mappingDetailsOpen(row.source.key)" class="mt-3 rounded-lg border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 overflow-hidden">
                <div class="px-3 py-2 border-b border-gray-100 dark:border-gray-800">
                  <p class="text-xs font-semibold text-ink-primary">Field movement</p>
                  <p class="text-[11px] text-gray-400">These are name-based suggestions only. They need approval before import.</p>
                </div>
                <div class="divide-y divide-gray-100 dark:divide-gray-800">
                  <div
                    v-for="fieldRow in mappingFieldRows(row).slice(0, 8)"
                    :key="`${row.source.key}-${fieldRow.sourceField}-${fieldRow.targetField || 'missing'}`"
                    class="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-3 py-2 text-xs"
                  >
                    <span class="font-medium text-ink-primary truncate">{{ fieldRow.sourceField }}</span>
                    <svg class="w-3.5 h-3.5 text-gray-300 dark:text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>
                    <span :class="['truncate', fieldRow.targetField ? 'text-ink-primary' : 'text-amber-600 dark:text-amber-300']">{{ fieldRow.targetField || 'Needs target field' }}</span>
                  </div>
                </div>
                <div v-if="missingRequiredTargetFields(row).length" class="px-3 py-2 border-t border-amber-100 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30">
                  <p class="text-[11px] font-semibold text-amber-700 dark:text-amber-300">Required target fields still need a source, default, or transform:</p>
                  <p class="text-[11px] text-amber-700 dark:text-amber-300 mt-1">{{ missingRequiredTargetFields(row).map(field => field.label || field.fieldname).join(', ') }}</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
          <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
            <p class="text-sm font-semibold text-ink-primary">Target Input</p>
            <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
              {{ selectedTargetSchema?.schema?.doctype_count || 0 }} DocTypes · {{ selectedTargetSchema?.schema?.writable_field_count || 0 }} writable fields
            </p>
          </div>
          <div class="divide-y divide-gray-100 dark:divide-gray-800 max-h-[620px] overflow-y-auto">
            <div
              v-for="doctypeSchema in targetDoctypeSchemas"
              :key="doctypeSchema.doctype"
              class="px-4 py-3"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <p class="text-sm font-medium text-ink-primary truncate">{{ doctypeSchema.doctype }}</p>
                  <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                    {{ doctypeSchema.field_count }} fields · {{ doctypeSchema.writable_field_count }} writable · {{ requiredTargetFields(doctypeSchema).length }} required
                  </p>
                </div>
              </div>
              <div class="flex flex-wrap gap-1.5 mt-3">
                <span
                  v-for="field in writableTargetFields(doctypeSchema).slice(0, 7)"
                  :key="field.fieldname || field.label"
                  :class="['px-2 py-1 rounded-md text-[11px]', field.reqd ? 'bg-red-50 dark:bg-red-950 text-red-600 dark:text-red-300' : 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300']"
                >{{ field.label || field.fieldname }}</span>
                <span v-if="writableTargetFields(doctypeSchema).length > 7" class="px-2 py-1 rounded-md bg-gray-50 dark:bg-gray-950 text-[11px] text-gray-400">
                  +{{ writableTargetFields(doctypeSchema).length - 7 }}
                </span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div class="flex justify-between mt-8">
        <button @click="step = 1" class="px-6 py-2.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">Back</button>
        <button
          @click="startMigration"
          class="px-6 py-2.5 bg-primary-500 hover:bg-primary-600 text-white rounded-lg text-sm font-medium transition-colors"
        >Continue to Migration Plan</button>
      </div>
    </div>

    <!-- Step 4: Migrate -->
    <div v-if="step === 3">
      <h2 class="text-xl font-bold mb-1">Migrate</h2>
      <p class="text-gray-500 dark:text-gray-400 text-sm mb-6">Write the approved mapping into the target system.</p>

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
        <div v-if="!migrationLogs.length" class="text-gray-600">Waiting for target write/import to start...</div>
      </div>
    </div>

    <!-- Step 5: Validation -->
    <div v-if="step === 4">
      <div class="text-center mb-8">
        <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-50 dark:bg-green-950 mb-4">
          <svg class="w-8 h-8 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <h2 class="text-xl font-bold mb-1">Migration Complete</h2>
        <p class="text-gray-500 dark:text-gray-400 text-sm">Your data has been written or imported successfully.</p>
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
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import StepIndicator from '@/components/StepIndicator.vue'
import SourceCard from '@/components/SourceCard.vue'
import FileUploadRow from '@/components/FileUploadRow.vue'
import {
  testTallyConnection,
  fetchTallyData,
  executeMigration,
  getMigrationStatus,
  getTargetCompanies,
  getIntegrationSourceTypes,
  getMigrationTargetTypes,
  getMigrationTargetSchema,
  createIntegrationTallyPairing,
  listIntegrationConnections,
  createSapHanaDiscoverySnapshot,
  extractSapHanaMasterData,
  planSapHanaMasterMigration,
  deleteIntegrationConnection as deleteIntegrationConnectionApi,
  getIntegrationConnectionStatus,
  getIntegrationSourceSchema,
} from '@/services/api.js'

const stepLabels = ['Source', 'Target', 'Mapping', 'Migrate', 'Validate']
const step = ref(0)
const headerMounted = ref(false)
const WIZARD_STATE_KEY = 'agentapp_migration.newMigrationWizard.v1'
let restoringWizardState = false
onMounted(async () => {
  headerMounted.value = !!document.querySelector('header .flex-1')
  await Promise.all([loadSourceTypes(), loadTargetTypes()])
  await restoreWizardState()
})

// Step 1: Source
const source = ref('')
const sourceTypes = ref([])
const loadingSourceTypes = ref(false)
const sourceTypesError = ref('')
const tallyMode = ref('bridge')
const selectedSourceType = computed(() => sourceTypes.value.find(item => item.source_key === source.value) || null)
const sourceTitle = computed(() => selectedSourceType.value?.title || 'the source')
const sourceLabel = computed(() => sourceTitle.value)

// Bridge mode
const bridgePairing = ref(null)
const creatingPairing = ref(false)
const bridgeError = ref('')
const bridgeServerUrl = import.meta.env.VITE_BRIDGE_SERVER_URL || import.meta.env.VITE_FRAPPE_BASE_URL || window.location.origin
const copiedTarget = ref('')
const tallyConnections = ref([])
const loadingTallyConnections = ref(false)
const tallyConnectionsError = ref('')
const tallyConnectionsNotice = ref('')
const deletingConnectionId = ref('')
const bridgeDiscovery = ref(null)
const loadingBridgeDiscovery = ref(false)
const bridgeDiscoveryError = ref('')
const bridgeAutoRefreshActive = ref(false)
const tallyDiscoveryItems = ref([])
let bridgeAutoRefreshInterval = null
const selectedBridgeConnectionId = computed(() => bridgePairing.value?.connection_id || '')
const bridgePowerShellCommand = computed(() => {
  const pairingCode = bridgePairing.value?.pairing_code || '<PAIRING_CODE>'
  return `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass\n.\\install_bridge_windows.ps1 -Server "${bridgeServerUrl}" -PairingCode "${pairingCode}" -TallyHost "localhost" -TallyPort 9000 -RunOnce`
})
const selectedTallyDiscoveryItems = computed(() => tallyDiscoveryItems.value.filter(item => item.enabled))
const bridgeDiscoverySubtitle = computed(() => {
  const connection = bridgeDiscovery.value?.connection
  if (!connection) return 'Waiting for the bridge to claim the pairing code.'
  const company = connection.tally_company_name || 'Tally company'
  const status = connection.status || 'Waiting'
  return `${company} · ${status}`
})

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

// SAP / generic Excel source modes
const sapConnectionLabel = ref('')
const sapConnections = ref([])
const loadingSapConnections = ref(false)
const creatingSapSnapshot = ref(false)
const extractingSapMasters = ref(false)
const sapError = ref('')
const sapNotice = ref('')
const selectedSapConnection = ref(null)
const selectedSapConnectionId = computed(() => selectedSapConnection.value?.name || '')
const sapDiscoveryItems = ref([])
const selectedSapDiscoveryItems = computed(() => sapDiscoveryItems.value.filter(item => item.enabled))
const excelWorkbook = ref(null)

async function loadSourceTypes() {
  loadingSourceTypes.value = true
  sourceTypesError.value = ''
  try {
    sourceTypes.value = await getIntegrationSourceTypes()
  } catch (e) {
    sourceTypes.value = []
    sourceTypesError.value = e.message || 'The source catalog API failed.'
  } finally {
    loadingSourceTypes.value = false
  }
}

function selectSource(sourceType) {
  source.value = sourceType.source_key
  target.value = ''
  selectedCompany.value = null
  previewEntities.value = []
  if (source.value === 'tally' && tallyMode.value === 'bridge') {
    loadTallyConnections()
  }
  if (source.value === 'sap') {
    loadSapConnections()
  }
}

function selectTallyMode(mode) {
  tallyMode.value = mode
  if (mode === 'bridge') loadTallyConnections()
}

const canProceedFromSource = computed(() => {
  if (source.value === 'sap') return !!selectedSapConnectionId.value && selectedSapDiscoveryItems.value.length > 0
  if (source.value === 'excel') return !!excelWorkbook.value
  if (source.value !== 'tally') return false
  if (tallyMode.value === 'bridge') return !!bridgePairing.value && selectedTallyDiscoveryItems.value.length > 0
  if (tallyMode.value === 'xml') return false
  if (tallyMode.value === 'live') return connectionStatus.value === 'ok'
  if (tallyMode.value === 'excel') return false
  return false
})

async function loadSapConnections() {
  loadingSapConnections.value = true
  sapError.value = ''
  sapNotice.value = ''
  try {
    const rows = await listIntegrationConnections()
    sapConnections.value = rows.filter(row => row.source_type === 'SAP')
    if (!selectedSapConnection.value && sapConnections.value.length) {
      await resumeSapConnection(sapConnections.value[0])
    }
  } catch (e) {
    sapConnections.value = []
    sapError.value = readableConnectionError(e, 'Could not load saved SAP sources')
  } finally {
    loadingSapConnections.value = false
  }
}

async function resumeSapConnection(connection) {
  selectedSapConnection.value = connection
  sapConnectionLabel.value = connection.connection_label || connection.name
  sapError.value = ''
  sapNotice.value = ''
  try {
    const schema = await getIntegrationSourceSchema({
      connectionId: connection.name,
      sourceKey: 'sap',
      sampleLimit: 3,
    })
    sapDiscoveryItems.value = buildSapDiscoveryItems(schema)
  } catch (e) {
    sapDiscoveryItems.value = []
    sapError.value = e.message || 'Could not load SAP source preview'
  }
}

async function createSapSnapshot() {
  creatingSapSnapshot.value = true
  sapError.value = ''
  sapNotice.value = ''
  try {
    const result = await createSapHanaDiscoverySnapshot(sapConnectionLabel.value || 'LLM SAP HANA Live')
    sapNotice.value = `Discovery refreshed: ${Object.values(result.tables || {}).filter(table => table.exists).length} SAP tables found.`
    await loadSapConnections()
    const connection = sapConnections.value.find(row => row.name === result.connection_id)
    if (connection) await resumeSapConnection(connection)
  } catch (e) {
    sapError.value = e.message || 'Could not refresh SAP discovery'
  } finally {
    creatingSapSnapshot.value = false
  }
}

async function extractSapMasters() {
  if (!selectedSapConnectionId.value) return
  extractingSapMasters.value = true
  sapError.value = ''
  sapNotice.value = ''
  try {
    const result = await extractSapHanaMasterData(selectedSapConnectionId.value)
    sapNotice.value = `Extracted ${formatNumber(result.total_inserted)} SAP master records.`
    await resumeSapConnection(selectedSapConnection.value)
  } catch (e) {
    sapError.value = e.message || 'Could not extract SAP master data'
  } finally {
    extractingSapMasters.value = false
  }
}

function buildSapDiscoveryItems(schema) {
  const definitions = {
    'SAP Branch': 'Branches and GST business places.',
    'SAP Account': 'Chart of accounts hierarchy and account balances.',
    'SAP BP Group': 'Customer and supplier group masters.',
    'SAP Business Partner': 'Customers, suppliers, and account-linked partners.',
    'SAP BP Address': 'Billing and shipping addresses with GSTIN data.',
    'SAP Item Group': 'Item group masters.',
    'SAP UOM': 'Units of measure.',
    'SAP Warehouse': 'Warehouses and inventory locations.',
    'SAP Item': 'Item masters, inventory flags, UOMs, batches, and stock summary fields.',
  }
  return (schema.record_types || []).map(recordType => ({
    key: recordType.record_type,
    label: recordType.record_type.replace(/^SAP /, ''),
    objectType: recordType.record_type,
    detail: definitions[recordType.record_type] || 'SAP master data.',
    count: Number(recordType.count || 0),
    fields: recordType.fields || [],
    samples: normalizePreviewSamples(recordType.samples || []),
    enabled: Number(recordType.count || 0) > 0,
  }))
}

async function createBridgePairing() {
  creatingPairing.value = true
  bridgeError.value = ''
  try {
    bridgePairing.value = await createIntegrationTallyPairing('Pending Tally bridge pairing')
    bridgeDiscovery.value = null
    bridgeDiscoveryError.value = ''
    tallyDiscoveryItems.value = []
    startBridgeAutoRefresh()
  } catch (e) {
    bridgeError.value = e.message || 'Could not create pairing code'
  } finally {
    creatingPairing.value = false
  }
}

async function copyToClipboard(text, target) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  copiedTarget.value = target
  window.setTimeout(() => {
    if (copiedTarget.value === target) copiedTarget.value = ''
  }, 1500)
}

async function loadTallyConnections() {
  loadingTallyConnections.value = true
  tallyConnectionsError.value = ''
  tallyConnectionsNotice.value = ''
  try {
    const rows = await listIntegrationConnections()
    tallyConnections.value = rows.filter(row => row.source_type === 'Tally')
  } catch (e) {
    tallyConnections.value = []
    tallyConnectionsError.value = readableConnectionError(e, 'Could not load saved Tally connections')
  } finally {
    loadingTallyConnections.value = false
  }
}

async function deleteTallyConnection(connection) {
  const label = connection.tally_company_name || connection.connection_label || connection.name
  if (!window.confirm(`Delete ${label} and its discovered source data?`)) return
  deletingConnectionId.value = connection.name
  tallyConnectionsError.value = ''
  tallyConnectionsNotice.value = ''
  try {
    await deleteIntegrationConnectionApi(connection.name)
    if (selectedBridgeConnectionId.value === connection.name) {
      resetBridgeSelection()
    }
    tallyConnectionsNotice.value = `Deleted ${label}.`
    await loadTallyConnections()
    tallyConnectionsNotice.value = `Deleted ${label}.`
  } catch (e) {
    tallyConnectionsError.value = readableConnectionError(e, 'Could not delete Tally connection')
  } finally {
    deletingConnectionId.value = ''
  }
}

function resetBridgeSelection() {
  stopBridgeAutoRefresh()
  bridgePairing.value = null
  bridgeDiscovery.value = null
  bridgeDiscoveryError.value = ''
  tallyDiscoveryItems.value = []
}

async function resumeTallyConnection(connection) {
  bridgePairing.value = {
    connection_id: connection.name,
    pairing_code: '',
  }
  bridgeDiscovery.value = { connection }
  bridgeError.value = ''
  bridgeDiscoveryError.value = ''
  tallyDiscoveryItems.value = []
  stopBridgeAutoRefresh()
  await refreshTallyDiscovery()
}

async function refreshTallyDiscovery() {
  if (!bridgePairing.value?.connection_id) return
  if (loadingBridgeDiscovery.value) return
  loadingBridgeDiscovery.value = true
  bridgeDiscoveryError.value = ''
  try {
    const status = await getIntegrationConnectionStatus(bridgePairing.value.connection_id)
    bridgeDiscovery.value = status
    tallyDiscoveryItems.value = buildTallyDiscoveryItems(status)
    if (tallyDiscoveryItems.value.some(item => item.count > 0)) {
      stopBridgeAutoRefresh()
    }
  } catch (e) {
    bridgeDiscoveryError.value = e.message || 'Could not refresh discovery'
    stopBridgeAutoRefresh()
  } finally {
    loadingBridgeDiscovery.value = false
  }
}

function startBridgeAutoRefresh() {
  stopBridgeAutoRefresh()
  bridgeAutoRefreshActive.value = true
  let attempts = 0
  bridgeAutoRefreshInterval = window.setInterval(async () => {
    attempts += 1
    await refreshTallyDiscovery()
    if (attempts >= 60) stopBridgeAutoRefresh()
  }, 5000)
}

function stopBridgeAutoRefresh() {
  if (bridgeAutoRefreshInterval) {
    window.clearInterval(bridgeAutoRefreshInterval)
    bridgeAutoRefreshInterval = null
  }
  bridgeAutoRefreshActive.value = false
}

function buildTallyDiscoveryItems(status) {
  const counts = status.object_counts || []
  const countFor = (objectType) => counts
    .filter(row => row.object_type === objectType)
    .reduce((sum, row) => sum + Number(row.count || 0), 0)
  const definitions = [
    { key: 'groups', label: 'Account Groups', objectType: 'Group', detail: 'Tally account hierarchy and parent groups.' },
    { key: 'ledgers', label: 'Ledgers', objectType: 'Ledger', detail: 'Accounts, customers, suppliers, duties, cash, bank, sales, purchases.' },
    { key: 'voucher_types', label: 'Voucher Types', objectType: 'Voucher Type', detail: 'Sales, purchase, receipt, payment, journal, contra, and custom voucher types.' },
    { key: 'vouchers', label: 'Vouchers', objectType: 'Voucher', detail: 'Transactions including invoices, payments, journals, stock vouchers, and orders where Tally exposes them.' },
    { key: 'stock_groups', label: 'Stock Groups', objectType: 'Stock Group', detail: 'Inventory grouping hierarchy.' },
    { key: 'stock_categories', label: 'Stock Categories', objectType: 'Stock Category', detail: 'Inventory category dimensions.' },
    { key: 'stock_items', label: 'Stock Items', objectType: 'Stock Item', detail: 'Inventory item masters discovered from Tally.' },
    { key: 'units', label: 'Units', objectType: 'Unit', detail: 'Units of measure and decimal settings.' },
    { key: 'godowns', label: 'Godowns', objectType: 'Godown', detail: 'Warehouses and inventory locations.' },
    { key: 'cost_categories', label: 'Cost Categories', objectType: 'Cost Category', detail: 'Cost allocation category masters.' },
    { key: 'cost_centres', label: 'Cost Centres', objectType: 'Cost Centre', detail: 'Cost allocation dimensions.' },
    { key: 'currencies', label: 'Currencies', objectType: 'Currency', detail: 'Currency masters and decimal settings.' },
  ]
  return definitions.map(definition => {
    const count = definition.count ?? countFor(definition.objectType)
    return {
      ...definition,
      count,
      enabled: count > 0,
    }
  })
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(String(value).replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function summarizeConnectionError(value) {
  if (!value) return ''
  let parsed = value
  if (typeof parsed === 'string') {
    try {
      parsed = JSON.parse(parsed)
    } catch {
      return parsed.length > 180 ? `${parsed.slice(0, 180)}...` : parsed
    }
  }
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const entries = Object.entries(parsed).filter(([, message]) => !!message)
    if (!entries.length) return ''
    const summary = entries
      .slice(0, 4)
      .map(([stream, message]) => {
        const text = String(message || '')
        if (text.includes('Remote end closed') || text.includes('Tally closed')) return `${stream}: Tally closed the connection`
        if (text.includes('MandatoryError') || text.includes('Value missing')) return `${stream}: source payload was empty`
        return `${stream}: ${text.split('\n')[0].slice(0, 80)}`
      })
      .join('; ')
    const remaining = entries.length > 4 ? `; ${entries.length - 4} more` : ''
    return `${summary}${remaining}`
  }
  return String(value)
}

function readableConnectionError(error, fallback) {
  const message = error?.message || ''
  if (message.includes('Login required') || message.includes('not whitelisted') || message.includes('not permitted')) {
    return 'Log in to Sena/Frappe in this browser, then reload saved Tally connections.'
  }
  return message || fallback
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
const targetTypes = ref([])
const loadingTargetTypes = ref(false)
const targetTypesError = ref('')
const companies = ref([])
const selectedCompany = ref(null)
const loadingCompanies = ref(false)
const fetchingPreview = ref(false)
const targetSchema = ref(null)
const loadingTargetSchema = ref(false)
const targetSchemaError = ref('')
const expandedMappingKey = ref('')
const selectedTargetSchema = computed(() => (
  targetSchema.value?.target?.target_key === target.value ? targetSchema.value : null
))
const previewEntities = ref([])
const targetDoctypeSchemas = computed(() => selectedTargetSchema.value?.schema?.doctypes || [])
const targetDoctypesByName = computed(() => new Map(
  targetDoctypeSchemas.value.map(doctypeSchema => [doctypeSchema.doctype, doctypeSchema]),
))

function sourceFieldName(field) {
  if (!field) return ''
  if (typeof field === 'string') return field
  if (typeof field !== 'object') return String(field)
  const value = firstReadableFieldValue(field, ['field', 'fieldname', 'field_name', 'label', 'name', 'key', 'path', 'id'])
  if (value) return value
  try {
    return JSON.stringify(field).slice(0, 80)
  } catch {
    return 'Unknown field'
  }
}

function firstReadableFieldValue(record, keys) {
  for (const key of keys) {
    const value = record?.[key]
    const label = readableFieldValue(value)
    if (label) return label
  }
  return ''
}

function readableFieldValue(value) {
  if (value == null || value === '') return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return readableFieldValue(value[0])
  if (typeof value === 'object') {
    return firstReadableFieldValue(value, ['field', 'fieldname', 'field_name', 'label', 'name', 'key', 'path', 'id'])
  }
  return ''
}

function sourceFieldCount(entity) {
  if (entity?.fields?.length) return entity.fields.length
  const sample = entity?.samples?.[0]
  if (sample?.record && typeof sample.record === 'object') {
    const record = sample.record.record || sample.record
    return Object.keys(record || {}).length
  }
  return 0
}

function writableTargetFields(doctypeSchema) {
  return (doctypeSchema?.fields || []).filter(field => field.writable !== false && !field.read_only)
}

function requiredTargetFields(doctypeSchema) {
  return writableTargetFields(doctypeSchema).filter(field => field.reqd || field.required)
}

function findTargetDoctype(...names) {
  for (const name of names) {
    const schema = targetDoctypesByName.value.get(name)
    if (schema) return schema
  }
  return null
}

function suggestTargetForSource(entity) {
  const key = `${entity?.key || ''} ${entity?.label || ''}`.toLowerCase()
  if (key.includes('customer')) return { target: findTargetDoctype('Customer'), confidence: 'High' }
  if (key.includes('supplier') || key.includes('vendor')) return { target: findTargetDoctype('Supplier'), confidence: 'High' }
  if (key.includes('stock item') || key.includes('item') || key.includes('product')) return { target: findTargetDoctype('Item'), confidence: 'High' }
  if (key.includes('stock group') || key.includes('item group') || key.includes('category')) return { target: findTargetDoctype('Item Group'), confidence: 'High' }
  if (key.includes('godown') || key.includes('warehouse')) return { target: findTargetDoctype('Warehouse'), confidence: 'High' }
  if (key.includes('cost centre') || key.includes('cost center')) return { target: findTargetDoctype('Cost Center'), confidence: 'High' }
  if (key.includes('ledger') || key.includes('group') || key.includes('account')) return { target: findTargetDoctype('Account'), confidence: key.includes('ledger') ? 'Review' : 'High' }
  if (key.includes('voucher') || key.includes('journal')) return { target: findTargetDoctype('Journal Entry', 'Sales Invoice', 'Purchase Invoice'), confidence: 'Review' }
  if (key.includes('address')) return { target: findTargetDoctype('Address'), confidence: 'High' }
  if (key.includes('contact')) return { target: findTargetDoctype('Contact'), confidence: 'High' }
  return { target: null, confidence: 'Unmapped' }
}

const mappingRows = computed(() => previewEntities.value.map(entity => {
  const suggestion = suggestTargetForSource(entity)
  return {
    source: entity,
    target: suggestion.target,
    confidence: suggestion.target ? suggestion.confidence : 'Unmapped',
    requiredFields: requiredTargetFields(suggestion.target),
  }
}))

function toggleMappingDetails(sourceKey) {
  expandedMappingKey.value = expandedMappingKey.value === sourceKey ? '' : sourceKey
}

function mappingDetailsOpen(sourceKey) {
  return expandedMappingKey.value === sourceKey
}

function normalizeFieldMatchKey(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
}

function targetFieldLabel(field) {
  return field?.label || field?.fieldname || ''
}

function suggestTargetField(sourceField, targetFields) {
  const sourceKey = normalizeFieldMatchKey(sourceField)
  if (!sourceKey) return null
  const exact = targetFields.find(field => normalizeFieldMatchKey(field.fieldname) === sourceKey || normalizeFieldMatchKey(field.label) === sourceKey)
  if (exact) return exact
  return targetFields.find(field => {
    const fieldName = normalizeFieldMatchKey(field.fieldname)
    const label = normalizeFieldMatchKey(field.label)
    return fieldName.includes(sourceKey) || label.includes(sourceKey) || sourceKey.includes(fieldName) || sourceKey.includes(label)
  }) || null
}

function mappingFieldRows(row) {
  if (!row?.target) return []
  const targetFields = writableTargetFields(row.target)
  const sourceFields = (row.source.fields || []).map(sourceFieldName).filter(Boolean)
  return sourceFields.map(sourceField => {
    const targetField = suggestTargetField(sourceField, targetFields)
    return {
      sourceField,
      targetField: targetFieldLabel(targetField),
      targetFieldMeta: targetField,
    }
  })
}

function missingRequiredTargetFields(row) {
  const mappedTargetFields = new Set(mappingFieldRows(row).map(fieldRow => normalizeFieldMatchKey(fieldRow.targetField)).filter(Boolean))
  return requiredTargetFields(row?.target).filter(field => {
    const fieldName = normalizeFieldMatchKey(field.fieldname)
    const label = normalizeFieldMatchKey(field.label)
    return !mappedTargetFields.has(fieldName) && !mappedTargetFields.has(label)
  })
}

async function loadTargetTypes() {
  loadingTargetTypes.value = true
  targetTypesError.value = ''
  try {
    targetTypes.value = await getMigrationTargetTypes()
  } catch (e) {
    targetTypes.value = []
    targetTypesError.value = e.message || 'The migration target registry API failed.'
  } finally {
    loadingTargetTypes.value = false
  }
}

async function selectTarget(t) {
  const targetType = targetTypes.value.find(item => item.target_key === t)
  if (targetType?.implementation_status !== 'Ready') return
  target.value = t
  targetSchemaError.value = ''
  targetSchema.value = null
  loadingTargetSchema.value = true
  const requestedTargetKey = t
  const schemaPromise = getMigrationTargetSchema(t)
  if (t === 'sena_erp' && !companies.value.length) {
    loadingCompanies.value = true
    try {
      companies.value = await getTargetCompanies()
      if (companies.value.length === 1) {
        selectedCompany.value = companies.value[0]
      }
    } catch (e) {
      companies.value = []
    } finally {
      loadingCompanies.value = false
    }
  }
  try {
    const schema = await schemaPromise
    if (target.value === requestedTargetKey) {
      targetSchema.value = schema
    }
  } catch (e) {
    if (target.value === requestedTargetKey) {
      targetSchema.value = null
      targetSchemaError.value = e.message || 'Could not load target schema'
    }
  } finally {
    if (target.value === requestedTargetKey) {
      loadingTargetSchema.value = false
    }
  }
}

const companyName = computed(() => selectedCompany.value?.name || '')
const companyAbbr = computed(() => selectedCompany.value?.abbr || '')

const canProceedFromTarget = computed(() => {
  return target.value === 'sena_erp' && !!selectedCompany.value
})

function serializeWizardState() {
  return {
    step: Math.min(Number(step.value || 0), 2),
    source: source.value,
    tallyMode: tallyMode.value,
    bridgePairing: bridgePairing.value,
    bridgeDiscovery: bridgeDiscovery.value,
    tallyDiscoveryItems: tallyDiscoveryItems.value,
    target: target.value,
    selectedCompany: selectedCompany.value,
    previewEntities: previewEntities.value,
  }
}

function saveWizardState() {
  if (restoringWizardState) return
  try {
    localStorage.setItem(WIZARD_STATE_KEY, JSON.stringify(serializeWizardState()))
  } catch {
    // Ignore storage failures in private windows.
  }
}

async function restoreWizardState() {
  let saved = null
  try {
    saved = JSON.parse(localStorage.getItem(WIZARD_STATE_KEY) || 'null')
  } catch {
    saved = null
  }
  if (!saved || typeof saved !== 'object') return

  restoringWizardState = true
  try {
    source.value = saved.source || ''
    tallyMode.value = saved.tallyMode || 'bridge'
    bridgePairing.value = saved.bridgePairing || null
    bridgeDiscovery.value = saved.bridgeDiscovery || null
    tallyDiscoveryItems.value = Array.isArray(saved.tallyDiscoveryItems) ? saved.tallyDiscoveryItems : []
    target.value = saved.target || ''
    selectedCompany.value = saved.selectedCompany || null
    previewEntities.value = Array.isArray(saved.previewEntities) ? saved.previewEntities : []

    if (source.value === 'tally' && tallyMode.value === 'bridge') {
      loadTallyConnections()
    }
    if (target.value) {
      await selectTarget(target.value)
      if (saved.selectedCompany) {
        const restoredCompany = companies.value.find(company => company.name === saved.selectedCompany.name)
        selectedCompany.value = restoredCompany || saved.selectedCompany
      }
    }
    step.value = Math.min(Number(saved.step || 0), previewEntities.value.length ? 2 : 1)
  } finally {
    restoringWizardState = false
    saveWizardState()
  }
}

watch(
  [
    step,
    source,
    tallyMode,
    bridgePairing,
    bridgeDiscovery,
    tallyDiscoveryItems,
    target,
    selectedCompany,
    previewEntities,
  ],
  saveWizardState,
  { deep: true },
)

async function fetchPreview() {
  fetchingPreview.value = true
  try {
    if (source.value === 'sap') {
      if (!selectedSapConnectionId.value) throw new Error('Select a SAP source connection first.')
      if (!sapDiscoveryItems.value.length) await resumeSapConnection(selectedSapConnection.value)
      previewEntities.value = selectedSapDiscoveryItems.value.map(item => ({
        key: item.key,
        label: item.label,
        count: item.count,
        enabled: true,
        expanded: !!item.samples?.length,
        fields: item.fields || [],
        samples: item.samples || [],
      }))
      step.value = 2
      return
    }
    if (source.value === 'excel') {
      previewEntities.value = [
        { key: 'workbook', label: 'Uploaded Workbook', count: 1, enabled: true, expanded: false,
          samples: [{ name: excelWorkbook.value?.name || 'Workbook' }] },
        { key: 'sheets', label: 'Sheets', count: 0, enabled: true, expanded: false, samples: [] },
        { key: 'headers', label: 'Detected Columns', count: 0, enabled: true, expanded: false, samples: [] },
        { key: 'rows', label: 'Source Rows', count: 0, enabled: true, expanded: false, samples: [] },
      ]
      step.value = 2
      return
    }
    if (tallyMode.value === 'bridge') {
      const connectionId = bridgePairing.value?.connection_id
      if (!connectionId) throw new Error('Select a Tally bridge connection first.')
      if (!bridgeDiscovery.value) await refreshTallyDiscovery()
      const schema = await getIntegrationSourceSchema({
        connectionId,
        sourceKey: 'tally',
        sampleLimit: 5,
      })
      const recordTypesByName = new Map((schema.record_types || []).map(item => [item.record_type, item]))
      previewEntities.value = selectedTallyDiscoveryItems.value.map(item => {
        const recordType = recordTypesByName.get(item.objectType)
        return {
          key: item.key,
          label: item.label,
          count: recordType?.count ?? item.count,
          enabled: true,
          expanded: !!recordType?.samples?.length,
          fields: recordType?.fields || [],
          samples: normalizePreviewSamples(recordType?.samples || []),
        }
      })
      step.value = 2
      return
    }
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

function normalizePreviewSamples(samples) {
  return samples.map(sample => {
    const record = sample.record || {}
    const inner = record.record || record
    const detail = [
      inner.parent,
      inner.vouchertypename,
      inner.date,
      inner.baseunits,
      inner.category,
      inner.CardType,
      inner.City,
      inner.InvntItem,
      inner.WhsCode,
    ].filter(Boolean).join(' · ')
    return {
      name: sample.record_name || inner.name || inner.vouchernumber || inner.ItemName || inner.CardName || inner.AcctName || sample.source_id,
      source_id: sample.source_id,
      record_name: sample.record_name,
      detail,
      parent: inner.parent,
      balance: inner.closingbalance ?? inner.closing_balance ?? inner.Balance ?? inner.CurrTotal ?? null,
      record,
    }
  })
}

function formatNumber(n) {
  if (n == null) return ''
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(n)
}

// Step 4: Progress
const migrationTasks = ref([])
const migrationLogs = ref([])
const overallProgress = ref(0)
let pollInterval = null

function addLog(message, level = 'info') {
  migrationLogs.value.push({ time: new Date().toLocaleTimeString(), message, level })
}

async function startMigration() {
  step.value = 3
  overallProgress.value = 0
  migrationTasks.value = [
    { label: 'Starting target write/import...', status: 'running', detail: '' },
  ]
  migrationLogs.value = []
  addLog('Starting target write/import...')

  try {
    if (source.value === 'sap') {
      const selectedRecordTypes = previewEntities.value
        .filter(item => item.enabled)
        .map(item => item.key)
      addLog('Planning SAP master-data migration...')
      migrationTasks.value = [
        { label: 'SAP source selected', status: 'done', detail: sapConnectionLabel.value },
        { label: 'Source records', status: 'running', detail: selectedRecordTypes.join(', ') },
        { label: 'Target mapping', status: 'pending', detail: companyName.value },
        { label: 'Target write/import', status: 'pending', detail: 'Dry-run only' },
      ]
      const plan = await planSapHanaMasterMigration({
        connectionId: selectedSapConnectionId.value,
        company: companyName.value,
        selectedRecordTypes,
      })
      overallProgress.value = 75
      migrationTasks.value = [
        { label: 'SAP source selected', status: 'done', detail: sapConnectionLabel.value },
        { label: 'Source records', status: 'done', detail: `${formatNumber(plan.totals.source_count)} records` },
        { label: 'Target mapping', status: 'done', detail: `${formatNumber(plan.totals.planned_count)} planned` },
        { label: 'Target write/import', status: 'pending', detail: 'Dry-run only' },
      ]
      addLog(`Dry-run plan ready: ${formatNumber(plan.totals.create_count)} new, ${formatNumber(plan.totals.existing_count)} existing, ${formatNumber(plan.totals.skipped_count)} skipped.`)
      plan.record_types
        .filter(item => item.source_count || item.planned_count)
        .forEach(item => {
          addLog(`${item.label}: ${formatNumber(item.create_count)} new / ${formatNumber(item.existing_count)} existing / ${formatNumber(item.skipped_count)} skipped`)
        })
      return
    }
    if (source.value === 'excel') {
      addLog('CSV / Excel source selected. Source capture is next.')
      overallProgress.value = 10
      migrationTasks.value = [
        { label: 'CSV / Excel source selected', status: 'done', detail: excelWorkbook.value?.name || '' },
        { label: 'Source capture', status: 'pending', detail: 'Not wired yet' },
        { label: 'Discovery sync', status: 'pending', detail: 'Source objects and sample records' },
        { label: 'Target mapping', status: 'pending', detail: 'Analytics and SenaERP targets' },
      ]
      return
    }
    if (tallyMode.value === 'bridge') {
      addLog('Source snapshot plan selected. Target write/import is next.')
      overallProgress.value = 20
      migrationTasks.value = [
        { label: 'Bridge pairing', status: 'done', detail: bridgePairing.value.connection_id },
        { label: 'Selected source objects', status: 'done', detail: selectedTallyDiscoveryItems.value.map(item => item.label).join(', ') },
        { label: 'Target schema mapping', status: 'pending', detail: 'Not implemented yet' },
        { label: 'Target write/import', status: 'pending', detail: 'Will not run before target confirmation' },
      ]
      return
    }
    const result = await executeMigration(
      tallyHost.value, tallyPort.value,
      companyName.value, companyAbbr.value,
      false
    )

    const jobId = result.job_id
    if (!jobId) {
      // Synchronous result (no background job) -- handle directly
      handleMigrationResult(result)
      return
    }

    addLog(`Migration job started: ${jobId}`)

    // Poll for progress every 2 seconds
    pollInterval = setInterval(async () => {
      try {
        const status = await getMigrationStatus(jobId)

        overallProgress.value = status.progress || 0

        if (status.current_step) {
          // Update the task label to show current step
          migrationTasks.value[0].label = status.current_step
        }

        if (status.steps && status.steps.length) {
          migrationTasks.value = status.steps.map(s => ({
            label: s.label || s,
            status: s.status || 'pending',
            detail: s.detail || '',
          }))
        }

        if (status.status === 'done') {
          clearInterval(pollInterval)
          pollInterval = null
          addLog('Migration complete!', 'success')
          handleMigrationResult(status)
        }

        if (status.status === 'error') {
          clearInterval(pollInterval)
          pollInterval = null
          addLog(`Error: ${status.current_step || 'Unknown error'}`, 'error')
          migrationTasks.value[0].status = 'error'
          migrationTasks.value[0].detail = status.current_step || 'Migration failed'
        }
      } catch (pollErr) {
        // Don't stop polling on transient errors
        console.warn('Poll error:', pollErr)
      }
    }, 2000)
  } catch (e) {
    migrationTasks.value[0].status = 'error'
    migrationTasks.value[0].detail = e.message || 'Failed to start migration'
    addLog(`Error: ${e.message || e}`, 'error')
  }
}

function handleMigrationResult(result) {
  validationStats.value = []
  if (result.created != null) validationStats.value.push({ label: 'Created', value: result.created })
  if (result.skipped != null) validationStats.value.push({ label: 'Skipped', value: result.skipped })
  if (result.errors != null) validationStats.value.push({ label: 'Errors', value: result.errors })

  if (result.error_details && result.error_details.length) {
    validationErrors.value = result.error_details.map(e => typeof e === 'string' ? e : JSON.stringify(e))
  }

  step.value = 4
}

onBeforeUnmount(() => {
  if (pollInterval) clearInterval(pollInterval)
  stopBridgeAutoRefresh()
})

// Step 5: Validation
const validationStats = ref([])
const validationErrors = ref([])

function goToStep(i) {
  if (i <= step.value) step.value = i
}
</script>
