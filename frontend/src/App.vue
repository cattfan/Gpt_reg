<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Languages, Moon, PanelLeftClose, PanelLeftOpen, Settings, ShieldCheck,
  Sun, UserPlus,
} from '@lucide/vue'

import { SUPPORTED_LOCALES, type AppLocale } from './i18n'
import ConfirmDialog from './components/ConfirmDialog.vue'
import ToastStack from './components/ToastStack.vue'
import { createPreferences, PREFERENCES_KEY, type ViewId } from './composables/usePreferences'
import { connectSse, disconnectSse } from './services/sse'
import CheckAccountsView from './views/CheckAccountsView.vue'
import RegistrationView from './views/RegistrationView.vue'
import SettingsView from './views/SettingsView.vue'

const { t, locale } = useI18n()
const preferences = createPreferences()
provide(PREFERENCES_KEY, preferences)

const views = [
  { id: 'registration' as const, label: 'nav.registration', icon: UserPlus },
  { id: 'checks' as const, label: 'nav.checks', icon: ShieldCheck },
  { id: 'settings' as const, label: 'nav.settings', icon: Settings },
]

const page = computed(() => ({
  registration: { title: t('registration.title'), subtitle: t('registration.subtitle') },
  checks: { title: t('checks.title'), subtitle: t('checks.subtitle') },
  settings: { title: t('settings.title'), subtitle: t('settings.subtitle') },
})[preferences.activeView.value])

function selectView(view: ViewId) { preferences.setActiveView(view) }
function toggleTheme() { preferences.setTheme(preferences.theme.value === 'dark' ? 'light' : 'dark') }
function changeLocale(event: Event) { preferences.setLocale((event.target as HTMLSelectElement).value as AppLocale) }

watch(preferences.locale, (value) => { locale.value = value }, { immediate: true })
onMounted(connectSse)
onBeforeUnmount(disconnectSse)
</script>

<template>
  <div class="app-shell" :class="{ 'rail-collapsed': preferences.railCollapsed.value }">
    <aside class="app-rail" :aria-label="t('nav.primary')">
      <div class="brand-lockup">
        <span class="brand-mark" aria-hidden="true">G</span>
        <span v-if="!preferences.railCollapsed.value" class="brand-copy">
          <strong>{{ t('app.name') }}</strong><small>{{ t('app.tagline') }}</small>
        </span>
      </div>

      <nav class="rail-nav">
        <button
          v-for="item in views" :key="item.id" type="button" class="rail-link"
          :class="{ active: preferences.activeView.value === item.id }"
          :aria-current="preferences.activeView.value === item.id ? 'page' : undefined"
          :data-view="item.id" :title="t(item.label)" @click="selectView(item.id)"
        >
          <component :is="item.icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
          <span v-if="!preferences.railCollapsed.value">{{ t(item.label) }}</span>
        </button>
      </nav>

      <div class="rail-footer">
        <label v-if="!preferences.railCollapsed.value" class="locale-control">
          <Languages :size="16" aria-hidden="true" />
          <span class="sr-only">{{ t('common.language') }}</span>
          <select data-testid="locale-select" :value="preferences.locale.value" @change="changeLocale">
            <option v-for="code in SUPPORTED_LOCALES" :key="code" :value="code">
              {{ code === 'vi' ? 'Tiếng Việt' : code === 'en' ? 'English' : '简体中文' }}
            </option>
          </select>
        </label>
        <button
          type="button" class="rail-action" data-testid="theme-toggle"
          :title="t('common.theme')" :aria-label="t('common.theme')" @click="toggleTheme"
        >
          <Sun v-if="preferences.theme.value === 'dark'" :size="17" aria-hidden="true" />
          <Moon v-else :size="17" aria-hidden="true" />
          <span v-if="!preferences.railCollapsed.value">{{ t(preferences.theme.value === 'dark' ? 'common.light' : 'common.dark') }}</span>
        </button>
        <button
          type="button" class="rail-action rail-collapse-action"
          :title="t(preferences.railCollapsed.value ? 'nav.expand' : 'nav.collapse')"
          :aria-label="t(preferences.railCollapsed.value ? 'nav.expand' : 'nav.collapse')"
          @click="preferences.setRailCollapsed(!preferences.railCollapsed.value)"
        >
          <PanelLeftOpen v-if="preferences.railCollapsed.value" :size="17" aria-hidden="true" />
          <PanelLeftClose v-else :size="17" aria-hidden="true" />
          <span v-if="!preferences.railCollapsed.value">{{ t('nav.collapse') }}</span>
        </button>
      </div>
    </aside>

    <div class="app-main">
      <header class="app-topbar">
        <div class="page-heading"><h1>{{ page.title }}</h1><p>{{ page.subtitle }}</p></div>
        <label class="mobile-locale">
          <Languages :size="16" aria-hidden="true" />
          <select data-testid="locale-select-mobile" :aria-label="t('common.language')" :value="preferences.locale.value" @change="changeLocale">
            <option value="vi">VI</option><option value="en">EN</option><option value="zh-CN">中文</option>
          </select>
        </label>
        <button type="button" class="mobile-theme" :aria-label="t('common.theme')" @click="toggleTheme">
          <Sun v-if="preferences.theme.value === 'dark'" :size="18" /><Moon v-else :size="18" />
        </button>
      </header>

      <main class="view-host">
        <span class="sr-only">{{ t('app.name') }}</span>
        <RegistrationView v-if="preferences.activeView.value === 'registration'" />
        <CheckAccountsView v-else-if="preferences.activeView.value === 'checks'" />
        <SettingsView v-else />
      </main>
    </div>

    <nav class="mobile-nav" :aria-label="t('nav.primary')">
      <button
        v-for="item in views" :key="item.id" type="button" :data-view="item.id"
        :class="{ active: preferences.activeView.value === item.id }"
        :aria-current="preferences.activeView.value === item.id ? 'page' : undefined"
        @click="selectView(item.id)"
      >
        <component :is="item.icon" :size="19" :stroke-width="1.8" aria-hidden="true" />
        <span>{{ t(item.label) }}</span>
      </button>
    </nav>
    <ToastStack />
    <ConfirmDialog />
  </div>
</template>
