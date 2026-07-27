<script setup lang="ts">
import { inject, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Eye, EyeOff, KeyRound, Languages, Moon, RefreshCw, Save, Sun } from '@lucide/vue'

import UiPanel from '../components/UiPanel.vue'
import { createPreferences, PREFERENCES_KEY } from '../composables/usePreferences'
import { showToast } from '../composables/useToast'
import { SUPPORTED_LOCALES, type AppLocale } from '../i18n'
import { apiJson, postJson, presentApiError } from '../services/api'
import type { SmsStatus } from '../types'

const { t, locale } = useI18n()
const preferences = inject(PREFERENCES_KEY, null) ?? createPreferences()
const proxyPool = ref('')
const rotationMode = ref('round_robin')
const smsKey = ref('')
const smsCountry = ref('')
const sms = ref<SmsStatus>({ configured: false })
const showKey = ref(false)
const savingProxy = ref(false)
const savingSms = ref(false)
const loadingSms = ref(false)
const hasSmsKey = ref(false)

function message(error: unknown) { return presentApiError(error, t) }
async function loadSettings() {
  try {
    const values = await apiJson<Record<string, string | null>>('/api/settings')
    proxyPool.value = values['proxy.pool'] || ''
    rotationMode.value = values['proxy.rotation_mode'] || 'round_robin'
    smsCountry.value = values['sms.smsbower.country'] || ''
    hasSmsKey.value = Boolean(values['sms.smsbower.api_key'])
  } catch (error) { showToast(message(error), 'danger') }
}
async function saveProxy() {
  savingProxy.value = true
  try {
    await postJson('/api/settings', { 'proxy.pool': proxyPool.value, 'proxy.rotation_mode': rotationMode.value })
    showToast(t('toast.saved'), 'success')
  } catch (error) { showToast(message(error), 'danger') }
  finally { savingProxy.value = false }
}
async function refreshSms() {
  loadingSms.value = true
  try {
    sms.value = await apiJson<SmsStatus>('/api/sms/status')
    if (sms.value.countries?.length && !sms.value.countries.some((country) => country.id === smsCountry.value)) smsCountry.value = sms.value.countries[0].id
    if (sms.value.error) showToast(`${t('toast.requestFailed')}: ${sms.value.error}`, 'danger')
  } catch (error) { showToast(message(error), 'danger') }
  finally { loadingSms.value = false }
}
async function saveSms() {
  savingSms.value = true
  try {
    const countryBeforeRefresh = smsCountry.value
    const patch: Record<string, unknown> = { 'sms.smsbower.country': smsCountry.value }
    if (smsKey.value.trim()) patch['sms.smsbower.api_key'] = smsKey.value.trim()
    await postJson('/api/settings', patch)
    hasSmsKey.value = hasSmsKey.value || Boolean(smsKey.value.trim())
    smsKey.value = ''
    showToast(t('toast.saved'), 'success')
    await refreshSms()
    if (!countryBeforeRefresh && smsCountry.value) {
      await postJson('/api/settings', { 'sms.smsbower.country': smsCountry.value })
    }
  } catch (error) { showToast(message(error), 'danger') }
  finally { savingSms.value = false }
}
function setLocale(value: AppLocale) { preferences.setLocale(value); locale.value = value }

onMounted(async () => { await loadSettings(); if (hasSmsKey.value) await refreshSms() })
</script>

<template>
  <div class="workspace settings-workspace" data-testid="settings-view">
    <div class="settings-grid">
      <UiPanel :title="t('settings.proxy')" :subtitle="t('settings.proxyHint')">
        <div class="form-stack">
          <label class="field"><span>{{ t('settings.proxyPool') }}</span><textarea v-model="proxyPool" class="mono-input settings-textarea" spellcheck="false" placeholder="user:pass@host:port" /></label>
          <label class="field"><span>{{ t('settings.rotation') }}</span><select v-model="rotationMode"><option value="round_robin">{{ t('settings.roundRobin') }}</option><option value="random">{{ t('settings.random') }}</option></select></label>
          <div class="action-row"><button class="btn primary" type="button" :disabled="savingProxy" @click="saveProxy"><Save :size="16" />{{ t('common.save') }}</button></div>
        </div>
      </UiPanel>

      <UiPanel :title="t('settings.sms')">
        <template #actions><button class="icon-btn" type="button" :title="t('common.refresh')" :disabled="loadingSms" @click="refreshSms"><RefreshCw :size="16" :class="{ spinning: loadingSms }" /></button></template>
        <div class="sms-summary">
          <div><span>{{ t('settings.balance') }}</span><strong>{{ sms.configured && sms.ok ? `$${sms.balance ?? 0}` : '-' }}</strong></div>
          <div><span>{{ t('settings.inventory') }}</span><strong>{{ sms.total_available ?? '-' }}</strong></div>
          <div><span>{{ t('settings.affordable') }}</span><strong>{{ sms.affordable ?? '-' }}</strong></div>
        </div>
        <div class="form-stack">
          <label class="field"><span><KeyRound :size="14" />{{ t('settings.apiKey') }}</span><div class="password-field"><input v-model="smsKey" :type="showKey ? 'text' : 'password'" autocomplete="new-password" :placeholder="sms.configured || hasSmsKey ? '••••••••••••' : 'SMSBower API key'"><button type="button" :title="t(showKey ? 'common.hide' : 'common.show')" :aria-label="t(showKey ? 'common.hide' : 'common.show')" @click="showKey = !showKey"><EyeOff v-if="showKey" :size="16" /><Eye v-else :size="16" /></button></div></label>
          <label class="field"><span>{{ t('settings.country') }}</span><select v-model="smsCountry" :disabled="!sms.countries?.length"><option value="">{{ t('settings.notConfigured') }}</option><option v-for="country in sms.countries || []" :key="country.id" :value="country.id">{{ country.name }} · ${{ country.cost }} · {{ country.count }}</option></select></label>
          <p v-if="!sms.configured" class="field-hint">{{ sms.reason || t('settings.notConfigured') }}</p>
          <div class="action-row"><button class="btn primary" type="button" :disabled="savingSms" @click="saveSms"><Save :size="16" />{{ t('common.save') }}</button></div>
        </div>
      </UiPanel>

      <UiPanel :title="t('settings.appearance')" class="appearance-panel">
        <div class="appearance-controls">
          <div class="setting-group"><span><Sun :size="16" />{{ t('common.theme') }}</span><div class="segmented"><button type="button" :class="{ active: preferences.theme.value === 'light' }" @click="preferences.setTheme('light')"><Sun :size="15" />{{ t('common.light') }}</button><button type="button" :class="{ active: preferences.theme.value === 'dark' }" @click="preferences.setTheme('dark')"><Moon :size="15" />{{ t('common.dark') }}</button></div></div>
          <div class="setting-group"><span><Languages :size="16" />{{ t('common.language') }}</span><div class="segmented locale-segment"><button v-for="code in SUPPORTED_LOCALES" :key="code" type="button" :class="{ active: preferences.locale.value === code }" @click="setLocale(code)">{{ code === 'vi' ? 'Tiếng Việt' : code === 'en' ? 'English' : '简体中文' }}</button></div></div>
        </div>
      </UiPanel>
    </div>
  </div>
</template>
