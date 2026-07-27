import { type InjectionKey, ref, watch } from 'vue'

import { resolveLocale, type AppLocale } from '../i18n'

export type Theme = 'light' | 'dark'
export type ViewId = 'registration' | 'checks' | 'settings'

const KEYS = {
  view: 'gptreg.view',
  theme: 'gptreg.theme',
  locale: 'gptreg.locale',
  rail: 'gptreg.rail.collapsed',
} as const

function read(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}

function write(key: string, value: string) {
  try { localStorage.setItem(key, value) } catch { /* storage may be unavailable */ }
}

export function resolveTheme(saved: string | null, systemDark: boolean): Theme {
  if (saved === 'light' || saved === 'dark') return saved
  return systemDark ? 'dark' : 'light'
}

export function createPreferences() {
  const systemDark = typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
  const storedView = read(KEYS.view)
  const activeView = ref<ViewId>(storedView === 'checks' || storedView === 'settings' ? storedView : 'registration')
  const theme = ref<Theme>(resolveTheme(read(KEYS.theme), systemDark))
  const locale = ref<AppLocale>(resolveLocale(read(KEYS.locale), navigator.language || 'vi'))
  const railCollapsed = ref(read(KEYS.rail) === '1')

  function setActiveView(value: ViewId) { activeView.value = value; write(KEYS.view, value) }
  function setTheme(value: Theme) { theme.value = value; write(KEYS.theme, value) }
  function setLocale(value: AppLocale) { locale.value = value; write(KEYS.locale, value) }
  function setRailCollapsed(value: boolean) { railCollapsed.value = value; write(KEYS.rail, value ? '1' : '0') }

  watch(theme, (value) => {
    document.documentElement.classList.toggle('dark', value === 'dark')
    document.documentElement.style.colorScheme = value
  }, { immediate: true })
  watch(locale, (value) => { document.documentElement.lang = value }, { immediate: true })

  return { activeView, theme, locale, railCollapsed, setActiveView, setTheme, setLocale, setRailCollapsed }
}

export type Preferences = ReturnType<typeof createPreferences>
export const PREFERENCES_KEY: InjectionKey<Preferences> = Symbol('preferences')
