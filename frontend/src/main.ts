import { createApp } from 'vue'

import App from './App.vue'
import { createAppI18n, resolveLocale } from './i18n'
import './styles.css'

let saved: string | null = null
try { saved = localStorage.getItem('gptreg.locale') } catch { /* storage may be unavailable */ }

createApp(App)
  .use(createAppI18n(resolveLocale(saved, navigator.language || 'vi')))
  .mount('#app')
