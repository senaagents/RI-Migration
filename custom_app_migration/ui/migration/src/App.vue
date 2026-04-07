<template>
  <div class="min-h-screen bg-sena-bg text-ink-primary">
    <header class="sticky top-0 z-10 bg-white/80 backdrop-blur-md" style="border-bottom: 1px solid var(--divider)">
      <div class="max-w-6xl mx-auto px-8 h-14 flex items-center gap-4">
        <!-- Back button (shown on wizard pages) -->
        <router-link
          v-if="$route.path !== '/'"
          to="/"
          class="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-black/[0.04] transition-colors -ml-1"
        >
          <svg class="w-4 h-4 text-ink-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
        </router-link>

        <!-- App title -->
        <div class="flex items-center gap-2.5">
          <svg class="w-5 h-5 text-primary-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2L2 7l10 5 10-5-10-5z"/>
            <path d="M2 17l10 5 10-5"/>
            <path d="M2 12l10 5 10-5"/>
          </svg>
          <span class="text-sm font-semibold tracking-tight text-ink-primary">Migration</span>
        </div>

        <!-- Step indicator (injected by child route via provide/inject) -->
        <div class="flex-1 flex justify-end">
          <slot name="header-right" />
        </div>
      </div>
    </header>
    <main>
      <router-view />
    </main>
  </div>
</template>
